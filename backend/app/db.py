"""SQLite storage for sessions and messages (stdlib sqlite3, one connection per call)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id    TEXT PRIMARY KEY,
    system_prompt TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,   -- 'user' or 'assistant'
    content     TEXT NOT NULL,
    metadata    TEXT,            -- JSON string, assistant messages only
    created_at  TEXT NOT NULL    -- server time, ISO-8601
);

CREATE INDEX IF NOT EXISTS idx_messages_session_id_id ON messages (session_id, id);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class StoredMessage:
    role: str
    content: str
    metadata: dict[str, Any] | None


@dataclass(frozen=True)
class Session:
    session_id: str
    system_prompt: str | None


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=5.0)
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
            yield conn
        finally:
            conn.close()

    def init_schema(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)

    def ping(self) -> bool:
        try:
            with self._connect() as conn:
                return conn.execute("SELECT 1").fetchone() == (1,)
        except sqlite3.Error:
            return False

    def get_session(self, session_id: str) -> Session | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT session_id, system_prompt FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return Session(*row) if row else None

    def get_messages(self, session_id: str) -> list[StoredMessage]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content, metadata FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [StoredMessage(role, content, json.loads(meta) if meta else None) for role, content, meta in rows]

    def save_turn(
        self,
        session_id: str,
        user_content: str,
        assistant_content: str,
        metadata: dict[str, Any],
        system_prompt: str | None = None,
    ) -> None:
        """Store one completed exchange atomically. A new system prompt replaces the session's."""
        now = utc_now()
        with self._connect() as conn, conn:  # second `conn` = transaction (commit / rollback)
            conn.execute(
                """
                INSERT INTO sessions (session_id, system_prompt, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (session_id) DO UPDATE SET
                    system_prompt = COALESCE(excluded.system_prompt, sessions.system_prompt),
                    updated_at = excluded.updated_at
                """,
                (session_id, system_prompt, now, now),
            )
            conn.executemany(
                "INSERT INTO messages (session_id, role, content, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
                [
                    (session_id, "user", user_content, None, now),
                    (session_id, "assistant", assistant_content, json.dumps(metadata), now),
                ],
            )
