"""
Planner — divide una tarea del usuario en subtareas con dependencias.
Usa el LLM para generar un plan estructurado en JSON.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from ..providers.base import LLMProvider, Message
from ..identity import SYSTEM_NAME

logger = logging.getLogger(__name__)

PLANNER_SYSTEM_PROMPT = f"""Eres un planificador de tareas para un sistema multi-agente.

Tu trabajo es analizar la solicitud del usuario y dividirla en subtareas claras.
Cada subtarea debe:
- Tener un agente específico asignado
- Listar sus dependencias (IDs de subtareas que deben completarse primero)
- Las tareas sin dependencias se ejecutarán en PARALELO

Agentes disponibles:
- "coder": Escribe código, refactoriza, lee/escribe archivos en el workspace, corre comandos/tests.
- "researcher": Busca información (web/arquitectura), lee archivos, resume contexto.
- "self_improver": Modifica el propio código del sistema {SYSTEM_NAME}.
- "generalist": Para tareas que no encajan en otra categoría.

IMPORTANTE: Si la tarea es simple y no necesita dividirse, devuelve UNA sola subtarea.
No sobre-dividas. Prefiere planes simples.

Debes responder ÚNICAMENTE con JSON válido, sin texto adicional, siguiendo este schema exacto:
{{
  "plan_summary": "descripción breve del plan",
  "tasks": [
    {{
      "id": "t1",
      "name": "nombre corto",
      "description": "descripción detallada de qué hacer",
      "agent": "coder|researcher|self_improver|generalist",
      "depends_on": []
    }}
  ]
}}"""


@dataclass
class TaskSpec:
    """Especificación de una subtarea dentro del plan."""

    id: str
    name: str
    description: str
    agent: str
    depends_on: list[str] = field(default_factory=list)
    # Campos de estado (se llenan durante ejecución)
    status: str = "pending"  # pending | running | done | failed
    result: str | None = None
    error: str | None = None

    def can_run(self, completed_ids: set[str]) -> bool:
        """True si todas las dependencias están completadas."""
        return all(dep in completed_ids for dep in self.depends_on)


@dataclass
class Plan:
    """Plan de ejecución generado por el planificador."""

    id: str
    summary: str
    tasks: list[TaskSpec]
    original_request: str

    def get_ready(self, completed_ids: set[str]) -> list[TaskSpec]:
        """Retorna tareas listas para ejecutar (pending + deps satisfechas)."""
        return [
            t for t in self.tasks
            if t.status == "pending" and t.can_run(completed_ids)
        ]

    def get_pending(self) -> list[TaskSpec]:
        return [t for t in self.tasks if t.status == "pending"]

    def is_complete(self) -> bool:
        return all(t.status in ("done", "failed") for t in self.tasks)

    def has_failures(self) -> bool:
        return any(t.status == "failed" for t in self.tasks)


class Planner:
    """
    Usa el LLM para dividir una solicitud en un Plan de subtareas.
    El plan determina qué agentes se usan y en qué orden.
    """

    def __init__(self, provider: LLMProvider):
        self.provider = provider

    async def create_plan(self, user_request: str, context: str = "") -> Plan:
        """Genera un Plan para la solicitud del usuario."""
        user_msg = user_request
        if context:
            user_msg = f"Contexto previo:\n{context}\n\nSolicitud: {user_request}"

        messages = [
            Message(role="system", content=PLANNER_SYSTEM_PROMPT),
            Message(role="user", content=user_msg),
        ]

        try:
            response = await self.provider.complete(
                messages=messages,
                tools=None,  # El planner no usa tools, solo genera JSON
                temperature=0.3,  # Baja temperatura para respuestas más deterministas
                max_tokens=2048,
            )

            raw = response.content or "{}"
            # Limpiar markdown code blocks si el LLM los añade
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            if raw.endswith("```"):
                raw = raw[:-3]

            data = json.loads(raw.strip())
            tasks = [
                TaskSpec(
                    id=t["id"],
                    name=t["name"],
                    description=t["description"],
                    agent=t.get("agent", "generalist"),
                    depends_on=t.get("depends_on", []),
                )
                for t in data.get("tasks", [])
            ]

            plan = Plan(
                id=str(uuid.uuid4())[:8],
                summary=data.get("plan_summary", user_request),
                tasks=tasks,
                original_request=user_request,
            )

            logger.info(
                "Plan creado: %d tareas (%s)",
                len(tasks),
                ", ".join(t.name for t in tasks),
            )
            return plan

        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Planner falló al parsear JSON (%s), usando plan simple de 1 tarea", e)
            # Fallback: plan de 1 sola tarea con el agente generalista
            return Plan(
                id=str(uuid.uuid4())[:8],
                summary=user_request,
                tasks=[
                    TaskSpec(
                        id="t1",
                        name="Tarea principal",
                        description=user_request,
                        agent="generalist",
                    )
                ],
                original_request=user_request,
            )
