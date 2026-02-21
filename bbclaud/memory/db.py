"""
Capa de memoria persistente con SQLite (aiosqlite).
Almacena: conversaciones, tareas, conocimiento acumulado y configuración.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    user_msg    TEXT NOT NULL,
    agent_msg   TEXT,
    metadata    TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    agent       TEXT,
    input       TEXT,
    result      TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS knowledge (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT UNIQUE NOT NULL,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""


class Database:
    """
    Gestiona el acceso async a SQLite.
    Singleton por path — compartido entre todos los módulos.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        if self._conn is not None:
            return
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        logger.info("Base de datos conectada: %s", self.db_path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        assert self._conn, "Llamá a connect() primero"
        cur = await self._conn.execute(sql, params)
        await self._conn.commit()
        return cur

    async def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        assert self._conn, "Llamá a connect() primero"
        cur = await self._conn.execute(sql, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        assert self._conn, "Llamá a connect() primero"
        cur = await self._conn.execute(sql, params)
        row = await cur.fetchone()
        return dict(row) if row else None

    # ── Conversaciones ────────────────────────────────────────────────────────

    async def save_conversation(
        self, user_msg: str, agent_msg: str, metadata: dict | None = None
    ) -> int:
        cur = await self.execute(
            "INSERT INTO conversations (user_msg, agent_msg, metadata) VALUES (?, ?, ?)",
            (user_msg, agent_msg, json.dumps(metadata or {})),
        )
        return cur.lastrowid

    async def get_recent_conversations(self, limit: int = 20) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM conversations ORDER BY id DESC LIMIT ?", (limit,)
        )

    # ── Tareas ────────────────────────────────────────────────────────────────

    async def upsert_task(self, task_id: str, name: str, **kwargs) -> None:
        fields = ["name"] + list(kwargs.keys())
        values = [name] + list(kwargs.values())
        placeholders = ", ".join(["?"] * len(values))
        cols = ", ".join(fields)
        await self.execute(
            f"INSERT OR REPLACE INTO tasks (id, {cols}, updated_at) "
            f"VALUES (?, {placeholders}, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))",
            (task_id, *values),
        )

    async def get_tasks(self, status: str | None = None) -> list[dict]:
        if status:
            return await self.fetchall(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC", (status,)
            )
        return await self.fetchall("SELECT * FROM tasks ORDER BY created_at DESC")

    # ── Conocimiento ──────────────────────────────────────────────────────────

    async def set_knowledge(self, key: str, value: Any) -> None:
        await self.execute(
            "INSERT OR REPLACE INTO knowledge (key, value, updated_at) "
            "VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))",
            (key, json.dumps(value)),
        )

    async def get_knowledge(self, key: str) -> Any | None:
        row = await self.fetchone("SELECT value FROM knowledge WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else None

    async def get_all_knowledge(self) -> dict[str, Any]:
        rows = await self.fetchall("SELECT key, value FROM knowledge")
        return {r["key"]: json.loads(r["value"]) for r in rows}

    # ── Config ────────────────────────────────────────────────────────────────

    async def set_config(self, key: str, value: Any) -> None:
        await self.execute(
            "INSERT OR REPLACE INTO config (key, value, updated_at) "
            "VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))",
            (key, json.dumps(value)),
        )

    async def get_config(self, key: str, default: Any = None) -> Any:
        row = await self.fetchone("SELECT value FROM config WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default
