"""SQLite helpers: connection, schema, re-entrant transactions, JSON columns."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from importlib import resources


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    schema = resources.files("agentgate").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)


@contextmanager
def tx(conn: sqlite3.Connection):
    """BEGIN/COMMIT/ROLLBACK. Nested use joins the outer transaction instead of failing."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def loads(s):
    return json.loads(s) if s is not None else None


def now_utc_day_bounds(now: int) -> tuple[int, int]:
    """[start, end) of the UTC calendar day containing ``now`` (unix seconds)."""
    start = now - (now % 86_400)
    return start, start + 86_400
