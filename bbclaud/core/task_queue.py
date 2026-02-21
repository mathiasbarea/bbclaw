"""
TaskQueue — ejecuta un Plan respetando dependencias entre tareas.
- Tareas sin dependencias pendientes → se ejecutan en PARALELO (asyncio.gather)
- Tareas con dependencias → se ejecutan SECUENCIALMENTE cuando las deps terminan
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .planner import Plan, TaskSpec
from .message_bus import bus, Event

if TYPE_CHECKING:
    from .agent import Agent

logger = logging.getLogger(__name__)


class TaskQueue:
    """
    Motor de ejecución de planes multi-agente.
    Soporta paralelismo real vía asyncio.gather para tasks independientes.
    """

    def __init__(self, agents: dict[str, "Agent"]):
        """
        agents: mapa de nombre → instancia de Agent.
        e.g. {"coder": CoderAgent(...), "researcher": ResearcherAgent(...)}
        """
        self.agents = agents

    async def execute(self, plan: Plan) -> Plan:
        """
        Ejecuta todas las tareas del plan respetando dependencias.
        Modifica el plan in-place (status, result, error de cada TaskSpec).
        Devuelve el plan con resultados completos.
        """
        completed_ids: set[str] = set()

        await bus.publish(Event("plan.started", "task_queue", {"plan_id": plan.id, "tasks": len(plan.tasks)}))
        logger.info("Ejecutando plan %s: %d tareas", plan.id, len(plan.tasks))

        while not plan.is_complete():
            ready = plan.get_ready(completed_ids)

            if not ready:
                # Si no hay tareas listas pero hay pendientes → deadlock o todas fallaron
                pending = plan.get_pending()
                if pending:
                    # Marcar tareas bloqueadas como fallidas
                    blocked_names = [t.name for t in pending]
                    logger.error("Deadlock detectado. Tareas bloqueadas: %s", blocked_names)
                    for task in pending:
                        task.status = "failed"
                        task.error = f"Deadlock: dependencias no satisfechas ({task.depends_on})"
                break

            if len(ready) == 1:
                # Ejecución secuencial (una sola tarea lista)
                await self._run_task(ready[0], plan)
                if ready[0].status == "done":
                    completed_ids.add(ready[0].id)
            else:
                # Ejecución paralela (múltiples tareas listas)
                logger.info("Ejecutando %d tareas en paralelo: %s", len(ready), [t.name for t in ready])
                await asyncio.gather(*[self._run_task(t, plan) for t in ready])
                for task in ready:
                    if task.status == "done":
                        completed_ids.add(task.id)

        await bus.publish(Event(
            "plan.completed",
            "task_queue",
            {
                "plan_id": plan.id,
                "success": not plan.has_failures(),
                "completed": len(completed_ids),
                "total": len(plan.tasks),
            },
        ))

        return plan

    async def _run_task(self, task: TaskSpec, plan: Plan) -> None:
        """Ejecuta una sola tarea usando el agente asignado."""
        from .agent import AgentContext  # evitar importación circular

        agent_name = task.agent
        agent = self.agents.get(agent_name) or self.agents.get("generalist")

        if not agent:
            task.status = "failed"
            task.error = f"Agente '{agent_name}' no disponible"
            return

        task.status = "running"
        await bus.publish(Event("task.started", agent_name, {"task_id": task.id, "name": task.name}))
        logger.info("Iniciando tarea '%s' con agente '%s'", task.name, agent_name)

        # Enriquecer la descripción con resultados de dependencias
        dep_context = self._build_dependency_context(task, plan)

        ctx = AgentContext(
            task_id=task.id,
            task_description=task.description,
            memory_context=dep_context,
        )

        try:
            result = await agent.run(ctx)
            if result.success:
                task.status = "done"
                task.result = result.output
                logger.info("Tarea '%s' completada (%d tool calls)", task.name, result.tool_calls_made)
                await bus.publish(Event("task.completed", agent_name, {"task_id": task.id, "output": result.output[:200]}))
            else:
                task.status = "failed"
                task.error = result.error
                logger.error("Tarea '%s' falló: %s", task.name, result.error)
                await bus.publish(Event("task.failed", agent_name, {"task_id": task.id, "error": result.error}))
        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            logger.error("Excepción en tarea '%s': %s", task.name, e)
            await bus.publish(Event("task.failed", agent_name, {"task_id": task.id, "error": str(e)}))

    def _build_dependency_context(self, task: TaskSpec, plan: Plan) -> str:
        """Construye contexto con resultados de las tareas de las que depende."""
        if not task.depends_on:
            return ""

        parts = ["## Resultados de tareas previas\n"]
        for dep_id in task.depends_on:
            dep = next((t for t in plan.tasks if t.id == dep_id), None)
            if dep and dep.result:
                parts.append(f"### {dep.name} ({dep_id})\n{dep.result[:1000]}\n")

        return "\n".join(parts) if len(parts) > 1 else ""
