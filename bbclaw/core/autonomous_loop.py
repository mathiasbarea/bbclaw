"""
Ciclo autónomo de procesamiento de proyectos con objective.
Corre en background cada tick_minutes (default 5 min):
  1. Procesa scheduled items y reminders (SIEMPRE, cada tick)
  2. Procesa objetivos de proyectos (frecuencia dinámica según cantidad)

Frecuencia de objetivos (tier-based):
  0 proyectos       → no procesa
  1-6 proyectos     → cada 60 min
  7-14 proyectos    → cada 30 min
  15-25 proyectos   → cada 15 min
  26-40 proyectos   → cada 10 min
  41+ proyectos     → cada 5 min

Cap diario: max_objective_runs_per_day (default 4) por proyecto en 24h.
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
        self._last_objective_run_at: object = None  # datetime from now_utc()
        self._objective_interval_minutes: int = 0

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
            "objectiveIntervalMinutes": self._objective_interval_minutes,
        }

    def _compute_objective_interval(self, project_count: int) -> int:
        """
        Intervalo (en minutos) entre procesamientos de objetivos,
        basado en cantidad de proyectos con objective activo.

        Tiers:
          0 proyectos       → 0 (no procesar)
          1-6 proyectos     → 60 min
          7-14 proyectos    → 30 min
          15-25 proyectos   → 15 min
          26-40 proyectos   → 10 min
          41+ proyectos     → 5 min
        """
        if project_count <= 0:
            return 0
        if project_count <= 6:
            return 60
        if project_count <= 14:
            return 30
        if project_count <= 25:
            return 15
        if project_count <= 40:
            return 10
        return 5

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

                # Tick fijo — siempre tick_minutes del config (default 5 min)
                cfg = self.orch.config.get("autonomous", {})
                tick = cfg.get("tick_minutes", 5)
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

                # Reset workspace al default para scheduled items
                from ..tools.filesystem import set_workspace
                default_ws = self.orch.config.get("workspace", {}).get("root", "workspace")
                set_workspace(default_ws)

                # SIEMPRE procesar scheduled items en cada tick
                await self._process_scheduled_items()

                # ── Procesamiento de objetivos (frecuencia dinámica) ─────
                projects = await self.orch.db.get_projects_with_objective()
                project_count = len(projects)
                interval = self._compute_objective_interval(project_count)
                self._objective_interval_minutes = interval

                if interval <= 0 or not projects:
                    continue

                now = now_utc()
                if self._last_objective_run_at is not None:
                    elapsed_min = (now - self._last_objective_run_at).total_seconds() / 60
                    if elapsed_min < interval:
                        continue

                # Filtrar proyectos que superaron el cap diario
                max_daily = cfg.get("max_objective_runs_per_day", 4)
                today = now.strftime("%Y-%m-%d")
                eligible = [
                    p for p in projects
                    if p.get("autonomous_runs_date") != today
                    or (p.get("autonomous_runs_today") or 0) < max_daily
                ]
                if not eligible:
                    continue

                proj = eligible[0]  # round-robin por last_autonomous_at ASC

                # Switch workspace al del proyecto antes de correr el agente
                set_workspace(proj["workspace_path"])

                self._running = True
                self._current_objective = proj["id"]
                try:
                    prompt = await self._build_objective_prompt(proj)
                    result = await asyncio.wait_for(
                        self.orch.run(prompt, intent="autonomous"),
                        timeout=300,
                    )
                    await self.orch.db.update_project_last_autonomous(proj["id"])
                    logger.info("Autonomous: progreso en proyecto '%s'", proj["name"])
                except asyncio.TimeoutError:
                    await self.orch.db.update_project_last_autonomous(proj["id"])
                    logger.warning("Autonomous: timeout en proyecto '%s'", proj["name"])
                except Exception as e:
                    logger.error("Error en autonomous loop: %s", e)
                finally:
                    self._running = False
                    self._current_objective = None

                self._last_objective_run_at = now

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Error en autonomous loop outer: %s", e)
                await asyncio.sleep(60)
