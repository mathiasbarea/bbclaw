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

CREATE TABLE IF NOT EXISTS projects (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    slug            TEXT UNIQUE NOT NULL,
    description     TEXT DEFAULT '',
    workspace_path  TEXT NOT NULL,
    last_used_at    TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS improvement_attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle       INTEGER NOT NULL,
    branch      TEXT,
    changed_files TEXT DEFAULT '[]',
    score_before REAL,
    score_after  REAL,
    merged      INTEGER DEFAULT 0,
    tokens_used INTEGER DEFAULT 0,
    error       TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS objectives (
    id          TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    priority    INTEGER DEFAULT 3,
    status      TEXT DEFAULT 'active',
    progress    TEXT DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS scheduled_items (
    id          TEXT PRIMARY KEY,
    item_type   TEXT NOT NULL DEFAULT 'task',
    title       TEXT NOT NULL,
    description TEXT DEFAULT '',
    schedule    TEXT NOT NULL,
    next_run_at TEXT,
    status      TEXT NOT NULL DEFAULT 'active',
    last_run_at TEXT,
    run_count   INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""


_global_db: "Database | None" = None


def get_db() -> "Database":
    """Retorna la instancia global de Database. Lanza RuntimeError si no fue inicializada."""
    if _global_db is None:
        raise RuntimeError("La base de datos todavía no fue inicializada. Llamá a Database() primero.")
    return _global_db


class Database:
    """
    Gestiona el acceso async a SQLite.
    Singleton por path — compartido entre todos los módulos.
    """

    def __init__(self, db_path: str | Path):
        global _global_db
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: aiosqlite.Connection | None = None
        _global_db = self

    async def connect(self) -> None:
        if self._conn is not None:
            return
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._run_migrations()
        await self._conn.commit()
        logger.info("Base de datos conectada: %s", self.db_path)

    async def _run_migrations(self) -> None:
        """Agrega columnas faltantes a tablas que ya existían. Idempotente."""
        migrations = [
            "ALTER TABLE improvement_attempts ADD COLUMN tokens_used INTEGER DEFAULT 0",
            "ALTER TABLE improvement_attempts ADD COLUMN score_before REAL",
            "ALTER TABLE improvement_attempts ADD COLUMN score_after REAL",
            "ALTER TABLE improvement_attempts ADD COLUMN merged INTEGER DEFAULT 0",
            "ALTER TABLE improvement_attempts ADD COLUMN error TEXT",
            "ALTER TABLE improvement_attempts ADD COLUMN changed_files TEXT DEFAULT '[]'",
            "ALTER TABLE objectives ADD COLUMN priority INTEGER DEFAULT 3",
            "ALTER TABLE objectives ADD COLUMN progress TEXT DEFAULT ''",
            "ALTER TABLE objectives ADD COLUMN updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))",
            "CREATE INDEX IF NOT EXISTS idx_scheduled_next_run ON scheduled_items(next_run_at) WHERE status = 'active'",
        ]
        for sql in migrations:
            try:
                await self._conn.execute(sql)
            except Exception:
                pass  # columna ya existe o tabla no existe aún — OK

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

    # ── Proyectos ─────────────────────────────────────────────────────────────

    async def get_all_projects(self) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM projects ORDER BY last_used_at DESC, created_at DESC"
        )

    async def get_project_by_slug(self, slug: str) -> dict | None:
        return await self.fetchone("SELECT * FROM projects WHERE slug = ?", (slug,))

    async def create_project(
        self,
        project_id: str,
        name: str,
        slug: str,
        description: str = "",
        workspace_path: str = "",
    ) -> None:
        await self.execute(
            "INSERT INTO projects (id, name, slug, description, workspace_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (project_id, name, slug, description, workspace_path),
        )

    async def update_project(
        self,
        project_id: str,
        name: str | None = None,
        slug: str | None = None,
        description: str | None = None,
        color: str | None = None,
    ) -> None:
        fields, vals = [], []
        if name is not None:
            fields.append("name = ?")
            vals.append(name)
        if slug is not None:
            fields.append("slug = ?")
            vals.append(slug)
        if description is not None:
            fields.append("description = ?")
            vals.append(description)
        if color is not None:
            fields.append("color = ?")
            vals.append(color)
        if not fields:
            return
        vals.append(project_id)
        await self.execute(
            f"UPDATE projects SET {', '.join(fields)} WHERE id = ?",
            tuple(vals),
        )

    async def update_project_last_used(self, project_id: str) -> None:
        await self.execute(
            "UPDATE projects SET last_used_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
            "WHERE id = ?",
            (project_id,),
        )

    async def delete_project(self, project_id: str) -> None:
        await self.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    # ── Improvement Attempts ───────────────────────────────────────────────────

    async def save_improvement_attempt(self, **kwargs) -> int:
        cols = list(kwargs.keys())
        vals = list(kwargs.values())
        placeholders = ", ".join(["?"] * len(vals))
        col_names = ", ".join(cols)
        cur = await self.execute(
            f"INSERT INTO improvement_attempts ({col_names}) VALUES ({placeholders})",
            tuple(vals),
        )
        return cur.lastrowid

    async def get_recent_improvement_attempts(self, limit: int = 10) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM improvement_attempts ORDER BY id DESC LIMIT ?", (limit,)
        )

    async def get_improvement_tokens_last_hour(self) -> int:
        row = await self.fetchone(
            "SELECT COALESCE(SUM(tokens_used), 0) AS total "
            "FROM improvement_attempts "
            "WHERE created_at >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-1 hour')"
        )
        return row["total"] if row else 0

    # ── Objectives ─────────────────────────────────────────────────────────────

    async def get_objectives(self, status: str | None = None) -> list[dict]:
        if status:
            return await self.fetchall(
                "SELECT * FROM objectives WHERE status = ? ORDER BY priority ASC, created_at DESC",
                (status,),
            )
        return await self.fetchall(
            "SELECT * FROM objectives ORDER BY priority ASC, created_at DESC"
        )

    async def add_objective(self, obj_id: str, description: str, priority: int = 3) -> None:
        await self.execute(
            "INSERT INTO objectives (id, description, priority) VALUES (?, ?, ?)",
            (obj_id, description, priority),
        )

    async def update_objective_status(
        self, obj_id: str, status: str, progress: str | None = None
    ) -> None:
        if progress is not None:
            await self.execute(
                "UPDATE objectives SET status = ?, progress = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
                (status, progress, obj_id),
            )
        else:
            await self.execute(
                "UPDATE objectives SET status = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
                (status, obj_id),
            )

    # ── Scheduled Items ────────────────────────────────────────────────────────

    async def create_scheduled_item(
        self,
        item_id: str,
        item_type: str,
        title: str,
        description: str,
        schedule: str,
        next_run_at: str | None,
    ) -> None:
        await self.execute(
            "INSERT INTO scheduled_items (id, item_type, title, description, schedule, next_run_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (item_id, item_type, title, description, schedule, next_run_at),
        )

    async def get_due_items(self, now_iso: str) -> list[dict]:
        rows = await self.fetchall(
            "SELECT * FROM scheduled_items WHERE status = 'active' AND next_run_at <= ? "
            "ORDER BY next_run_at ASC",
            (now_iso,),
        )
        for r in rows:
            try:
                r["schedule"] = json.loads(r["schedule"])
            except (json.JSONDecodeError, TypeError):
                pass
        return rows

    async def get_scheduled_items(self, status: str | None = None) -> list[dict]:
        if status:
            rows = await self.fetchall(
                "SELECT * FROM scheduled_items WHERE status = ? ORDER BY next_run_at ASC",
                (status,),
            )
        else:
            rows = await self.fetchall(
                "SELECT * FROM scheduled_items ORDER BY next_run_at ASC"
            )
        for r in rows:
            try:
                r["schedule"] = json.loads(r["schedule"])
            except (json.JSONDecodeError, TypeError):
                pass
        return rows

    async def get_scheduled_item(self, item_id: str) -> dict | None:
        row = await self.fetchone(
            "SELECT * FROM scheduled_items WHERE id = ?", (item_id,)
        )
        if row:
            try:
                row["schedule"] = json.loads(row["schedule"])
            except (json.JSONDecodeError, TypeError):
                pass
        return row

    async def update_scheduled_item(self, item_id: str, **kwargs) -> None:
        if not kwargs:
            return
        fields, vals = [], []
        for k, v in kwargs.items():
            fields.append(f"{k} = ?")
            vals.append(v)
        vals.append(item_id)
        await self.execute(
            f"UPDATE scheduled_items SET {', '.join(fields)} WHERE id = ?",
            tuple(vals),
        )

    async def delete_scheduled_item(self, item_id: str) -> None:
        await self.execute("DELETE FROM scheduled_items WHERE id = ?", (item_id,))
