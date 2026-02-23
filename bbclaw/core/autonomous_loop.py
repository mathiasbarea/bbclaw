"""
Ciclo autónomo de procesamiento de proyectos con objective.
Corre en background, toma proyectos con objective definido y trabaja en ellos
cuando el sistema está idle y no hay improvement corriendo.

Frecuencia dinámica: el tick se ajusta según la cantidad de proyectos con
objective, apuntando a que cada proyecto reciba atención cada
~target_minutes_per_project minutos (default 30).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .orchestrator import Orchestrator

logger = logging.getLogger(__name__)


class AutonomousLoop:
    """Background loop que procesa proyectos con objective definido."""

    def __init__(self, orchestrator: Orchestrator):
        self.orch = orchestrator
        self._task: asyncio.Task | None = None
        self._running = False
        self._current_objective: str | None = None
        self._last_tick_at: str | None = None
        self._tick_minutes: int = 5

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info("Autonomous loop iniciado")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Autonomous loop detenido")

    @property
    def status(self) -> dict:
        return {
            "isRunning": self._running,
            "currentObjective": self._current_objective,
            "activeObjectives": 0,  # overridden by API with real DB count
            "lastTickAt": self._last_tick_at,
            "tickMinutes": self._tick_minutes,
        }

    def _compute_tick(self, project_count: int) -> int:
        """
        Frecuencia dinámica basada en cantidad de proyectos con objective.

        Lógica: target_per_project / num_projects, con floor y ceiling.
          - 0 proyectos → ceiling (nada que hacer, tick lento)
          - 1 proyecto  → target_per_project (ej. 30 min)
          - 6 proyectos → floor (ej. 5 min)

        Config keys (en [autonomous]):
          tick_minutes              = 5   (floor — mínimo entre ticks)
          target_minutes_per_project = 30  (cada cuánto debería atenderse cada proyecto)
        """
        cfg = self.orch.config.get("autonomous", {})
        floor = cfg.get("tick_minutes", 5)
        ceiling = cfg.get("max_idle_tick_minutes", 60)
        target = cfg.get("target_minutes_per_project", 30)

        if project_count <= 0:
            return ceiling
        return max(floor, min(ceiling, target // project_count))

    async def _build_objective_prompt(self, proj: dict) -> str:
        """
        Construye el prompt para el agente autónomo, incluyendo historial
        de las últimas conversaciones autónomas sobre este proyecto.
        """
        lines = [
            f"Proyecto: {proj['name']}",
            f"Objetivo: {proj['objective']}",
        ]

        # Inyectar historial de trabajo previo
        if self.orch.db:
            try:
                recent = await self.orch.db.get_recent_autonomous_conversations(
                    proj["name"], limit=3
                )
                if recent:
                    lines.append("\n--- Trabajo previo (últimos ciclos) ---")
                    for conv in reversed(recent):
                        summary = (conv.get("agent_msg") or "")[:200]
                        if summary:
                            lines.append(f"• {summary}")
                    lines.append("---")
            except Exception:
                pass

        lines.append(
            "\nTrabajá en avanzar este objetivo. "
            "Hacé un paso concreto y pequeño que NO repita lo ya hecho."
        )
        return "\n".join(lines)

    async def _process_scheduled_items(self) -> None:
        """Process due scheduled items: fire reminders, run tasks."""
        if not self.orch.db:
            return
        try:
            from .scheduler import to_iso, now_utc, compute_next_run
            now = now_utc()
            due_items = await self.orch.db.get_due_items(to_iso(now))

            for item in due_items:
                item_id = item["id"]
                sched = item["schedule"] if isinstance(item["schedule"], dict) else {}

                if item["item_type"] == "reminder":
                    # Append to orchestrator pending reminders (no agent)
                    self.orch._pending_reminders.append({
                        "id": item_id,
                        "title": item["title"],
                        "fired_at": to_iso(now),
                    })
                    logger.info("Reminder fired: %s — %s", item_id, item["title"])
                else:
                    # Scheduled task — run via orchestrator
                    try:
                        await asyncio.wait_for(
                            self.orch.run(
                                item.get("description") or item["title"],
                                intent="autonomous",
                            ),
                            timeout=300,
                        )
                    except asyncio.TimeoutError:
                        logger.warning("Scheduled task timeout: %s", item_id)
                    except Exception as e:
                        logger.error("Scheduled task error %s: %s", item_id, e)

                # Update item: run_count++, last_run_at, compute next
                new_count = (item.get("run_count") or 0) + 1
                next_run = compute_next_run(sched, after=now)
                if next_run is None:
                    await self.orch.db.update_scheduled_item(
                        item_id,
                        status="done",
                        last_run_at=to_iso(now),
                        run_count=new_count,
                        next_run_at=None,
                    )
                else:
                    await self.orch.db.update_scheduled_item(
                        item_id,
                        last_run_at=to_iso(now),
                        run_count=new_count,
                        next_run_at=next_run,
                    )
        except Exception as e:
            logger.error("Error processing scheduled items: %s", e)

    async def _loop(self) -> None:
        # Esperar 60s al inicio para que el sistema se estabilice
        await asyncio.sleep(60)

        while True:
            try:
                from .scheduler import next_aligned_tick, now_utc, to_iso

                # Tick dinámico: recalcular en cada iteración
                project_count = 0
                if self.orch.db:
                    try:
                        projs = await self.orch.db.get_projects_with_objective()
                        project_count = len(projs)
                    except Exception:
                        pass

                tick = self._compute_tick(project_count)
                self._tick_minutes = tick

                target = next_aligned_tick(tick, now_utc())
                delay = (target - now_utc()).total_seconds()
                if delay > 0:
                    await asyncio.sleep(delay)

                self._last_tick_at = to_iso(now_utc())

                # No correr si improvement está activo
                if self.orch._improvement_running:
                    continue

                if not self.orch.db:
                    continue

                # Process scheduled items FIRST
                await self._process_scheduled_items()

                # Obtener proyectos con objective (round-robin: least-recently-processed first)
                projects = await self.orch.db.get_projects_with_objective()
                if not projects:
                    continue

                proj = projects[0]

                self._running = True
                self._current_objective = proj["id"]
                try:
                    prompt = await self._build_objective_prompt(proj)
                    result = await asyncio.wait_for(
                        self.orch.run(prompt, intent="autonomous"),
                        timeout=300,
                    )
                    # Marcar que este proyecto fue procesado (round-robin)
                    await self.orch.db.update_project_last_autonomous(proj["id"])
                    logger.info("Autonomous: progreso en proyecto '%s'", proj["name"])
                except asyncio.TimeoutError:
                    # Aún así marcar para no bloquear la rotación
                    await self.orch.db.update_project_last_autonomous(proj["id"])
                    logger.warning("Autonomous: timeout en proyecto '%s'", proj["name"])
                except Exception as e:
                    logger.error("Error en autonomous loop: %s", e)
                finally:
                    self._running = False
                    self._current_objective = None

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Error en autonomous loop outer: %s", e)
                await asyncio.sleep(60)
