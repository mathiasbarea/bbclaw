"""
Ciclo autónomo de procesamiento de objectives.
Corre en background, toma objectives activos y trabaja en ellos
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
    """Background loop que procesa objectives activos."""

    def __init__(self, orchestrator: Orchestrator):
        self.orch = orchestrator
        self._task: asyncio.Task | None = None
        self._running = False
        self._current_objective: str | None = None

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
            "activeObjectives": 0,  # se actualiza en _loop
        }

    async def _loop(self) -> None:
        # Esperar 60s al inicio para que el sistema se estabilice
        await asyncio.sleep(60)

        tick = self.orch.config.get("autonomous", {}).get("tick_minutes", 5)

        while True:
            try:
                await asyncio.sleep(tick * 60)

                # No correr si improvement está activo
                if self.orch._improvement_running:
                    continue

                if not self.orch.db:
                    continue

                # Obtener objectives activos
                objectives = await self.orch.db.get_objectives(status="active")
                if not objectives:
                    continue

                # Tomar el de mayor prioridad (menor número = más prioridad)
                obj = sorted(objectives, key=lambda o: o.get("priority", 3))[0]

                self._running = True
                self._current_objective = obj["id"]
                try:
                    result = await asyncio.wait_for(
                        self.orch.run(
                            f"Trabajá en este objetivo: {obj['description']}. "
                            f"Progreso previo: {obj.get('progress', 'ninguno')}",
                            intent="autonomous",
                        ),
                        timeout=300,  # 5 min max
                    )
                    # Actualizar progreso
                    progress = str(result)[:500] if result else ""
                    await self.orch.db.update_objective_status(
                        obj["id"], "active", progress=progress
                    )
                    logger.info("Autonomous: progreso en objective '%s'", obj["id"])
                except asyncio.TimeoutError:
                    logger.warning("Autonomous: timeout en objective '%s'", obj["id"])
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
