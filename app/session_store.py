"""
Session persistence — makes /api/rank sessions survive backend
restarts, and (with user_id added) lets each logged-in user see their
own history of past analyses ("New Analysis" runs) rather than a
single shared pool of sessions.

Backed by the shared Postgres pool (app/db.py). Each session is stored
as one JSON blob keyed by session_id, plus a user_id column so a
user's analyses can be listed without deserializing every session's
JSON blob just to check ownership.

Sessions older than SESSION_TTL_SECONDS are treated as expired and
skipped on load / removed on next prune — same "session expired,
re-run ranking" behavior as before.
"""

import json
import os
import time
from typing import Dict, List, Optional

from app.db import get_conn, put_conn

DB_PATH = "sessions.db"  # kept only for backward compatibility with existing
# callers that still pass db_path=DB_PATH -- ignored, storage goes through
# the shared Postgres pool (DATABASE_URL) instead of a SQLite file.
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days

# Hard cap on how many sessions load_all_sessions() pulls into memory at
# startup. Without this, every non-expired session (up to 7 days' worth)
# loads in full -- fit scores, every JD-section/resume-section similarity
# match, and the complete Gemini gap-analysis text per resume -- and NEVER
# gets evicted for the life of the process. On Render's free 512MB tier
# this is a direct OOM contributor: /api/health showed 20 sessions resident
# and climbing, on top of ~186MB of library baseline, leaving too little
# headroom for an actual ranking request (parsing files + running the
# embedding model) to complete without the process getting killed.
#
# This only caps what's preloaded at STARTUP. Sessions created during a
# long-running process (between Render spin-downs) still accumulate in
# main.py's in-memory SESSIONS dict with no eviction -- capping that too
# needs a matching change in main.py's /api/rank handler; not done here
# since this file doesn't touch that dict directly.
MAX_SESSIONS_IN_MEMORY = int(os.environ.get("MAX_SESSIONS_IN_MEMORY", "10"))


def _connect(db_path: str = DB_PATH):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            created_at DOUBLE PRECISION NOT NULL,
            user_id TEXT
        )
        """
    )
    # Add user_id to a pre-existing sessions table from before auth existed
    # (IF NOT EXISTS keeps this safe to run on every connect, and Postgres
    # supports it directly on ALTER TABLE ADD COLUMN).
    cur.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS user_id TEXT")
    conn.commit()
    cur.close()
    return conn


def save_session(session_id: str, session: dict, db_path: str = DB_PATH, user_id: Optional[str] = None) -> None:
    """Write-through persist: call this any time SESSIONS[session_id]
    is created or mutated (new ranking run, gap analysis regenerated,
    etc.) so the on-disk copy never falls behind memory.

    user_id: pass this on the FIRST save of a new session (right after
    /api/rank creates it) so it's tied to the logged-in user who ran
    it. On later saves (regenerating gap analysis, etc.) it's fine to
    omit -- the COALESCE below keeps the existing user_id rather than
    nulling it out.
    """
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO sessions (session_id, data, created_at, user_id) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (session_id) DO UPDATE SET data = EXCLUDED.data, created_at = EXCLUDED.created_at, "
            "user_id = COALESCE(EXCLUDED.user_id, sessions.user_id)",
            (session_id, json.dumps(session), time.time(), user_id),
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def load_session(session_id: str, db_path: str = DB_PATH) -> Optional[dict]:
    """Fetch one session from disk (used as a fallback if it's missing
    from the in-memory SESSIONS dict, e.g. right after a restart before
    load_all_sessions has run, or in a multi-process setup).
    """
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT data, created_at FROM sessions WHERE session_id = %s", (session_id,)
        )
        row = cur.fetchone()
        cur.close()
    finally:
        put_conn(conn)

    if row is None:
        return None
    data, created_at = row
    if time.time() - created_at > SESSION_TTL_SECONDS:
        return None
    return json.loads(data)


def load_all_sessions(db_path: str = DB_PATH, limit: int = MAX_SESSIONS_IN_MEMORY) -> Dict[str, dict]:
    """Load the most recent `limit` non-expired sessions from disk into
    a dict, meant to be called once at backend startup to repopulate
    the in-memory SESSIONS cache so existing session_ids keep working
    across a restart.

    Deliberately capped (not "every non-expired session") -- on a
    memory-constrained deploy, preloading an unbounded, ever-growing
    number of full session blobs (each with per-resume scores, section
    matches, and gap-analysis text) is a direct OOM risk. A session
    older than what fits in this cap isn't gone -- load_session(id)
    below still fetches it from Postgres on demand if a request comes
    in for it; it's just not held in RAM speculatively.
    """
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cutoff = time.time() - SESSION_TTL_SECONDS
        cur.execute(
            "SELECT session_id, data FROM sessions WHERE created_at > %s "
            "ORDER BY created_at DESC LIMIT %s",
            (cutoff, limit),
        )
        rows = cur.fetchall()
        cur.close()
    finally:
        put_conn(conn)

    return {session_id: json.loads(data) for session_id, data in rows}


def list_sessions_for_user(user_id: str, db_path: str = DB_PATH) -> List[dict]:
    """Return lightweight metadata (not the full session blob) for every
    non-expired analysis a user has run, newest first -- powers a
    "your past analyses" history list in the frontend without having
    to deserialize every session's full JSON just to show a list.
    """
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cutoff = time.time() - SESSION_TTL_SECONDS
        cur.execute(
            "SELECT session_id, data, created_at FROM sessions "
            "WHERE user_id = %s AND created_at > %s ORDER BY created_at DESC",
            (user_id, cutoff),
        )
        rows = cur.fetchall()
        cur.close()
    finally:
        put_conn(conn)

    out = []
    for session_id, data, created_at in rows:
        session = json.loads(data)
        out.append({
            "session_id": session_id,
            "created_at": created_at,
            "resume_count": len(session.get("ranked", [])),
            "doc_names": [r.get("doc_name") for r in session.get("ranked", [])],
        })
    return out


def prune_expired(db_path: str = DB_PATH) -> int:
    """Delete expired sessions from disk. Returns the number removed.
    Not called automatically anywhere critical — safe to run
    periodically (e.g. from a startup hook) to keep the DB small.
    """
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cutoff = time.time() - SESSION_TTL_SECONDS
        cur.execute("DELETE FROM sessions WHERE created_at <= %s", (cutoff,))
        removed = cur.rowcount
        conn.commit()
        cur.close()
        return removed
    finally:
        put_conn(conn)