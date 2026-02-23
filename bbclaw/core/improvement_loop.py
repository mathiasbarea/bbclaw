"""
Ciclo de auto-mejora del sistema.
Corre en background, detecta periodos de inactividad del usuario,
y ejecuta ciclos de mejora en branches improve/*.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .orchestrator import Orchestrator

logger = logging.getLogger(__name__)


class ImprovementLoop:
    """Background loop que auto-mejora el sistema."""

    def __init__(self, orchestrator: Orchestrator):
        self.orch = orchestrator
        self._task: asyncio.Task | None = None
        self._running = False
        self._cycle_count = 0
        self._last_run_at: str | None = None
        self._last_score_delta: float | None = None
        self._consecutive_no_improvement = 0

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info("Improvement loop iniciado")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Improvement loop detenido")

    @property
    def status(self) -> dict:
        tokens_last_hour = 0
        budget = self.orch.config.get("improvement", {}).get("token_budget_per_hour", 80000)
        return {
            "isRunning": self._running,
            "cycleCount": self._cycle_count,
            "consecutiveNoImprovement": self._consecutive_no_improvement,
            "lastRunAt": self._last_run_at,
            "lastScoreDelta": self._last_score_delta,
            "tokensLastHour": tokens_last_hour,
            "tokenBudget": budget,
        }

    async def _loop(self) -> None:
        # Esperar 30s al inicio para que el sistema se estabilice
        await asyncio.sleep(30)
        while True:
            try:
                await asyncio.sleep(60)  # check cada minuto
                if not await self._should_run():
                    continue
                self._running = True
                self.orch._improvement_running = True
                try:
                    await self._run_cycle()
                finally:
                    self._running = False
                    self.orch._improvement_running = False
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Error en improvement loop: %s", e)
                await asyncio.sleep(60)

    async def _should_run(self) -> bool:
        cfg = self.orch.config.get("improvement", {})
        if not cfg.get("enabled", True):
            return False

        # Check: no exceder max cycles por hora
        max_cycles = cfg.get("max_cycles_per_hour", 3)
        if await self._cycles_this_hour() >= max_cycles:
            return False

        # Check: token budget
        budget = cfg.get("token_budget_per_hour", 80000)
        if self.orch.db:
            tokens = await self.orch.db.get_improvement_tokens_last_hour()
            if tokens >= budget:
                return False

        # Check: inactividad del usuario
        idle_minutes = cfg.get("idle_minutes_before_run", 10)
        elapsed = (time.time() - self.orch._last_user_activity) / 60.0
        if elapsed < idle_minutes:
            return False

        # Check: no estar en branch improve/*
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "rev-parse", "--abbrev-ref", "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            branch = stdout.decode().strip()
            if branch.startswith("improve/"):
                # Cleanup: volver a main
                await self._git_checkout_main()
                return False
        except Exception:
            pass

        return True

    async def _cycles_this_hour(self) -> int:
        if not self.orch.db:
            return 0
        attempts = await self.orch.db.get_recent_improvement_attempts(limit=50)
        now = datetime.now(timezone.utc)
        count = 0
        for a in attempts:
            try:
                created = datetime.fromisoformat(a["created_at"].replace("Z", "+00:00"))
                if (now - created).total_seconds() < 3600:
                    count += 1
            except Exception:
                pass
        return count

    async def _run_cycle(self) -> None:
        self._cycle_count += 1
        cycle = self._cycle_count
        branch = f"improve/{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        error_msg: str | None = None
        merged = False
        changed_files: list[str] = []

        logger.info("Improvement cycle %d: creando branch %s", cycle, branch)

        try:
            # 1. Crear branch desde main
            await self._git_exec("git", "checkout", "-b", branch)

            # 2. Construir contexto y correr agente
            prompt = (
                "Sos el auto-improver del sistema. Analizá el código fuente en bbclaw/, "
                "identificá una mejora concreta (bug fix, optimización, feature pequeño), "
                "implementala y verificá que funciona. Hacé cambios pequeños y seguros."
            )
            try:
                result = await asyncio.wait_for(
                    self.orch.run(prompt, intent="improvement"),
                    timeout=300,  # 5 min max por ciclo
                )
            except asyncio.TimeoutError:
                error_msg = "Timeout: ciclo excedió 5 minutos"
                logger.warning(error_msg)
                return

            # 3. Ver cambios
            proc = await asyncio.create_subprocess_exec(
                "git", "diff", "--name-only", "main",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            changed_files = [f for f in stdout.decode().strip().split("\n") if f]

            if changed_files:
                # Commit cambios
                await self._git_exec("git", "add", "-A")
                await self._git_exec(
                    "git", "commit", "-m", f"improve: cycle {cycle}"
                )
                # Merge a main
                await self._git_exec("git", "checkout", "main")
                await self._git_exec("git", "merge", branch, "--no-edit")
                merged = True
                logger.info("Cycle %d merged: %s", cycle, changed_files)
            else:
                self._consecutive_no_improvement += 1
                logger.info("Cycle %d: sin cambios", cycle)

        except Exception as e:
            error_msg = str(e)
            logger.error("Error en improvement cycle %d: %s", cycle, e)
        finally:
            # Cleanup: siempre volver a main y borrar branch
            await self._git_checkout_main()
            try:
                await self._git_exec("git", "branch", "-D", branch)
            except Exception:
                pass

        # Guardar intento en DB
        self._last_run_at = datetime.now(timezone.utc).isoformat()
        if merged:
            self._consecutive_no_improvement = 0

        if self.orch.db:
            try:
                import json
                await self.orch.db.save_improvement_attempt(
                    cycle=cycle,
                    branch=branch,
                    changed_files=json.dumps(changed_files),
                    merged=1 if merged else 0,
                    error=error_msg,
                )
            except Exception as e:
                logger.error("Error guardando improvement attempt: %s", e)

    async def _git_exec(self, *args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            raise RuntimeError(f"git error: {stderr.decode().strip()}")
        return stdout.decode().strip()

    async def _git_checkout_main(self) -> None:
        try:
            await self._git_exec("git", "checkout", "main")
        except Exception:
            try:
                await self._git_exec("git", "checkout", "master")
            except Exception:
                pass
