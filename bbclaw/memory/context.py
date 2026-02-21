"""
Módulo de contexto: construye el contexto relevante para cada llamada al LLM.
Combina historial de conversaciones + memoria semántica.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .db import Database
    from .vectors import VectorMemory
    from ..providers.base import LLMProvider

logger = logging.getLogger(__name__)


class ContextBuilder:
    """
    Construye ventanas de contexto enriquecidas para el LLM.
    Combina:
    - Últimas N conversaciones (historial reciente)
    - Top K fragmentos de memoria semántica relevantes
    - Conocimiento acumulado relevante
    """

    def __init__(
        self,
        db: "Database",
        vectors: "VectorMemory | None" = None,
        provider: "LLMProvider | None" = None,
        recent_limit: int = 10,
        top_k: int = 5,
    ):
        self.db = db
        self.vectors = vectors
        self.provider = provider
        self.recent_limit = recent_limit
        self.top_k = top_k

    async def build(self, user_input: str) -> str:
        """
        Retorna un bloque de texto con el contexto relevante.
        Se inyecta en el system prompt del agente.
        """
        parts: list[str] = []

        # 1. Historial reciente
        recent = await self.db.get_recent_conversations(self.recent_limit)
        if recent:
            history_lines = []
            for conv in reversed(recent):  # más viejo primero
                history_lines.append(f"Usuario: {conv['user_msg']}")
                if conv.get("agent_msg"):
                    history_lines.append(f"Asistente: {conv['agent_msg']}")
            parts.append("## Historial reciente\n" + "\n".join(history_lines))

        # 2. Memoria semántica (si hay vectores disponibles)
        if self.vectors and self.provider:
            try:
                count = await self.vectors.count()
                if count > 0:
                    embedding = await self.provider.embed(user_input)
                    relevant = await self.vectors.search(embedding, k=self.top_k)
                    if relevant:
                        semantic_lines = [f"- {r['text']}" for r in relevant if r["distance"] < 1.2]
                        if semantic_lines:
                            parts.append(
                                "## Memoria semántica relevante\n" + "\n".join(semantic_lines)
                            )
            except Exception as e:
                logger.warning("Error al buscar memoria semántica: %s", e)

        # 3. Conocimiento acumulado
        knowledge = await self.db.get_all_knowledge()
        if knowledge:
            k_lines = [f"- **{k}**: {v}" for k, v in list(knowledge.items())[:10]]
            parts.append("## Conocimiento acumulado\n" + "\n".join(k_lines))

        return "\n\n".join(parts) if parts else ""
