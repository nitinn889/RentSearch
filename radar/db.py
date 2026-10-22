"""SQLite on your PC; Postgres when DATABASE_URL is set (hosted, so data survives restarts).

Queries are written once with `?` placeholders and portable SQL (ON CONFLICT works in both).
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, pw_hash TEXT NOT NULL, created TEXT NOT NULL)""",
    "CREATE TABLE IF NOT EXISTS settings (user_id TEXT PRIMARY KEY, data TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS listings (
        source TEXT NOT NULL, source_id TEXT NOT NULL, data TEXT NOT NULL, updated TEXT NOT NULL,
        PRIMARY KEY (source, source_id))""",
    """CREATE TABLE IF NOT EXISTS seen (
        user_id TEXT NOT NULL, source TEXT NOT NULL, source_id TEXT NOT NULL,
        first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, unread INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, source, source_id))""",
    """CREATE TABLE IF NOT EXISTS baselines (
        user_id TEXT NOT NULL, watch TEXT NOT NULL, feed TEXT NOT NULL, created TEXT NOT NULL,
        PRIMARY KEY (user_id, watch, feed))""",
    """CREATE TABLE IF NOT EXISTS buildings (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, data TEXT NOT NULL, created TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS scans (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, started TEXT NOT NULL, finished TEXT NOT NULL,
        summary TEXT NOT NULL, items TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS alert_log (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, sent_at TEXT NOT NULL, data TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS seen_unread ON seen (user_id, unread)",
    "CREATE INDEX IF NOT EXISTS buildings_by_user ON buildings (user_id)",
    "CREATE INDEX IF NOT EXISTS scans_by_user ON scans (user_id, finished)",
    "CREATE INDEX IF NOT EXISTS alerts_by_user ON alert_log (user_id, sent_at)",
]


class Conn:
    """One transaction's connection, with the placeholder style translated for Postgres."""

    def __init__(self, raw, postgres: bool):
        self.raw = raw
        self.postgres = postgres

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.postgres else sql

    def run(self, sql: str, params: Iterable[Any] = ()) -> int:
        return self.raw.execute(self._sql(sql), tuple(params)).rowcount

    def many(self, sql: str, rows: list[tuple]) -> None:
        if not rows:
            return
        if self.postgres:
            with self.raw.cursor() as cur:
                cur.executemany(self._sql(sql), rows)
        else:
            self.raw.executemany(sql, rows)

    def one(self, sql: str, params: Iterable[Any] = ()) -> tuple | None:
        return self.raw.execute(self._sql(sql), tuple(params)).fetchone()

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[tuple]:
        return list(self.raw.execute(self._sql(sql), tuple(params)).fetchall())


class Database:
    def __init__(self, url: str = "", sqlite_path: Path | None = None):
        self.postgres = url.startswith(("postgres://", "postgresql://"))
        self._lock = threading.Lock()
        if self.postgres:
            from psycopg_pool import ConnectionPool  # only needed when hosting with Postgres

            self._pool = ConnectionPool(url, min_size=0, max_size=4, open=True,
                                        check=ConnectionPool.check_connection, kwargs={"connect_timeout": 15})
        else:
            self.path = Path(url) if url else sqlite_path
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.tx() as conn:
            for statement in SCHEMA:
                conn.run(statement)

    @property
    def kind(self) -> str:
        return "postgres" if self.postgres else "sqlite"

    @contextmanager
    def tx(self) -> Iterator[Conn]:
        """A transaction: committed when the block ends, rolled back if it raises."""
        if self.postgres:
            with self._pool.connection() as raw:
                yield Conn(raw, True)
        else:
            with self._lock, closing(sqlite3.connect(self.path, timeout=30)) as raw:
                with raw:
                    yield Conn(raw, False)

    def close(self) -> None:
        if self.postgres:
            self._pool.close()
