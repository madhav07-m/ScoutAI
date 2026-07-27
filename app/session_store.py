"""
Session persistence — makes /api/rank sessions survive backend
restarts (and, by extension, the "switch back to dashboard, get
Session not found" bug that in-memory-only SESSIONS caused).

Same pattern already used for companies_store.py: a local SQLite file,
zero extra infra, fine for a single-user local app. Each session is
stored as one JSON blob keyed by session_id — the session dict is
already JSON-safe (strings, floats, and (resume_section, similarity)
tuples, which round-trip through JSON as 2-element lists and unpack
identically via `resume_section, sim = value`, so no special-casing
needed there).

Sessions older than SESSION_TTL_SECONDS are treated as expired and
skipped on load / removed on next prune — same "session expired,
re-run ranking" behavior as before, just on a much longer timescale
(days, not "until the server process happens to restart").
"""

import json
import sqlite3
import time
from typing import Dict, Optional

DB_PATH = "sessions.db"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days


def _connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    return conn


def save_session(session_id: str, session: dict, db_path: str = DB_PATH) -> None:
    """Write-through persist: call this any time SESSIONS[session_id]
    is created or mutated (new ranking run, gap analysis regenerated,
    etc.) so the on-disk copy never falls behind memory.
    """
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO sessions (session_id, data, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET data = excluded.data",
            (session_id, json.dumps(session), time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def load_session(session_id: str, db_path: str = DB_PATH) -> Optional[dict]:
    """Fetch one session from disk (used as a fallback if it's missing
    from the in-memory SESSIONS dict, e.g. right after a restart before
    load_all_sessions has run, or in a multi-process setup).
    """
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT data, created_at FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    data, created_at = row
    if time.time() - created_at > SESSION_TTL_SECONDS:
        return None
    return json.loads(data)


def load_all_sessions(db_path: str = DB_PATH) -> Dict[str, dict]:
    """Load every non-expired session from disk into a dict, meant to
    be called once at backend startup to repopulate the in-memory
    SESSIONS cache so existing session_ids keep working across a
    restart.
    """
    conn = _connect(db_path)
    try:
        cutoff = time.time() - SESSION_TTL_SECONDS
        rows = conn.execute(
            "SELECT session_id, data FROM sessions WHERE created_at > ?", (cutoff,)
        ).fetchall()
    finally:
        conn.close()

    return {session_id: json.loads(data) for session_id, data in rows}


def prune_expired(db_path: str = DB_PATH) -> int:
    """Delete expired sessions from disk. Returns the number removed.
    Not called automatically anywhere critical — safe to run
    periodically (e.g. from a startup hook) to keep the DB small.
    """
    conn = _connect(db_path)
    try:
        cutoff = time.time() - SESSION_TTL_SECONDS
        cur = conn.execute("DELETE FROM sessions WHERE created_at <= ?", (cutoff,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
