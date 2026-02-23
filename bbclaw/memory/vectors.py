"""
Memoria vectorial usando sqlite-vec.
Almacena embeddings para búsqueda semántica (RAG).
"""

from __future__ import annotations

import json
import logging
import struct
from pathlib import Path
from typing import Any

import aiosqlite
import sqlite_vec

logger = logging.getLogger(__name__)

VECTORS_SCHEMA = """
CREATE TABLE IF NOT EXISTS vec_documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT NOT NULL,
    metadata    TEXT DEFAULT '{}',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""

# La tabla virtual de vectores se crea dinámicamente según la dimensión del embedding


def _serialize(vec: list[float]) -> bytes:
    """Serializa un vector float32 para sqlite-vec."""
    return struct.pack(f"{len(vec)}f", *vec)


class VectorMemory:
    """
    Memoria semántica basada en sqlite-vec.
    Permite almacenar texto+embedding y buscar por similitud coseno.
    """

    def __init__(self, db_path: str | Path, embedding_dim: int = 384):
        self.db_path = Path(db_path)
        self.embedding_dim = embedding_dim
        self._conn: aiosqlite.Connection | None = None
        self._initialized = False

    async def connect(self) -> None:
        if self._conn:
            return
        self._conn = await aiosqlite.connect(self.db_path)

        # Cargar extensión sqlite-vec (aiosqlite 0.17+ expone estos métodos como async)
        await self._conn.enable_load_extension(True)
        await self._conn.load_extension(sqlite_vec.loadable_path())
        await self._conn.enable_load_extension(False)

        # Crear tablas
        await self._conn.executescript(VECTORS_SCHEMA)

        # Crear virtual table para vectores si no existe
        await self._conn.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_index
            USING vec0(embedding float[{self.embedding_dim}])
            """
        )
        await self._conn.commit()
        self._initialized = True
        logger.info("VectorMemory conectada (dim=%d): %s", self.embedding_dim, self.db_path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def store(
        self, text: str, embedding: list[float], metadata: dict | None = None
    ) -> int:
        """Guarda un documento con su embedding."""
        assert self._conn and self._initialized

        # Insertar documento
        cur = await self._conn.execute(
            "INSERT INTO vec_documents (text, metadata) VALUES (?, ?)",
            (text, json.dumps(metadata or {})),
        )
        doc_id = cur.lastrowid

        # Insertar vector en la tabla virtual (rowid debe coincidir)
        await self._conn.execute(
            "INSERT INTO vec_index (rowid, embedding) VALUES (?, ?)",
            (doc_id, _serialize(embedding)),
        )
        await self._conn.commit()
        return doc_id

    async def search(
        self, query_embedding: list[float], k: int = 5
    ) -> list[dict[str, Any]]:
        """
        Busca los K documentos más cercanos al embedding de consulta.
        Devuelve lista de dicts con text, metadata y distance.
        """
        assert self._conn and self._initialized

        cur = await self._conn.execute(
            """
            SELECT
                d.id,
                d.text,
                d.metadata,
                d.created_at,
                v.distance
            FROM vec_index v
            JOIN vec_documents d ON d.id = v.rowid
            WHERE v.embedding MATCH ? AND k = ?
            ORDER BY v.distance
            """,
            (_serialize(query_embedding), k),
        )
        rows = await cur.fetchall()
        return [
            {
                "id": r[0],
                "text": r[1],
                "metadata": json.loads(r[2]),
                "created_at": r[3],
                "distance": r[4],
            }
            for r in rows
        ]

    async def count(self) -> int:
        cur = await self._conn.execute("SELECT COUNT(*) FROM vec_documents")
        row = await cur.fetchone()
        return row[0] if row else 0
