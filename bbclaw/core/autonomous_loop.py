"""
Ciclo autónomo de procesamiento de proyectos con objective.
Corre en background, toma proyectos con objective definido y trabaja en ellos
cuando el sistema está idle y no hay improvement corriendo.
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

        tick = self.orch.config.get("autonomous", {}).get("tick_minutes", 5)
        self._tick_minutes = tick

        while True:
            try:
                # Use clock-aligned ticks instead of flat sleep
                from .scheduler import next_aligned_tick, now_utc, to_iso
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

                # Obtener proyectos con objective definido
                projects = await self.orch.db.get_projects_with_objective()
                if not projects:
                    continue

                # Tomar el más recientemente usado
                proj = projects[0]

                self._running = True
                self._current_objective = proj["id"]
                try:
                    result = await asyncio.wait_for(
                        self.orch.run(
                            f"Proyecto: {proj['name']}\nObjetivo: {proj['objective']}\n"
                            "Trabajá en avanzar este objetivo. Hacé un paso concreto y pequeño.",
                            intent="autonomous",
                        ),
                        timeout=300,
                    )
                    logger.info("Autonomous: progreso en proyecto '%s'", proj["name"])
                except asyncio.TimeoutError:
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
