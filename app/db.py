"""
Shared Postgres connection pool.

Replaces the two separate SQLite files (companies.db, sessions.db)
that companies_store.py and session_store.py used to manage on their
own. Both now go through this single pool instead, backed by one
Postgres database (e.g. a free Supabase project).

Set DATABASE_URL in your .env / Render environment to your Supabase
connection string, e.g.:
    DATABASE_URL=postgresql://postgres:[PASSWORD]@[HOST]:5432/postgres

Supabase specifically: Project Settings -> Database -> Connection
string -> "URI" tab gives you this directly (use the "Transaction"
pooler connection string if given the choice -- it's meant for exactly
this kind of short-lived-connection backend usage).
"""

import os
from psycopg2 import pool

_DATABASE_URL = os.environ.get("DATABASE_URL")
_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        if not _DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL is not set. Add it to your .env (locally) or "
                "your Render/host environment variables -- it should be your "
                "Supabase Postgres connection string."
            )
        # minconn=1, maxconn=10: small pool, fine for a single-instance
        # backend like this one. Raise maxconn if you later run multiple
        # backend workers/instances against the same database.
        _pool = pool.SimpleConnectionPool(1, 10, _DATABASE_URL)
    return _pool


def get_conn():
    """Borrow a connection from the pool. Always pair with put_conn()
    in a finally block, same pattern as the old sqlite3.connect()/
    conn.close() calls this replaces."""
    return _get_pool().getconn()


def put_conn(conn):
    """Return a connection to the pool (NOT the same as closing it --
    the pool keeps it open for reuse)."""
    _get_pool().putconn(conn)
