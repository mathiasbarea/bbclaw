"""
Orquestador principal del sistema de agentes — Fase 2.
Pipeline completo: contexto → planner → task_queue → síntesis → memoria.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import tomllib
from pathlib import Path
from typing import Any

from ..core.agent import Agent, AgentContext
from ..core.planner import Planner, Plan
from ..core.task_queue import TaskQueue
from ..core.message_bus import bus
from ..memory.db import Database
from ..memory.vectors import VectorMemory
from ..memory.context import ContextBuilder
from ..providers.base import LLMProvider
from ..providers.codex_oauth import CodexOAuthProvider
from ..providers.openai_api import OpenAIAPIProvider
from ..tools.registry import registry
from ..identity import SYSTEM_NAME

logger = logging.getLogger(__name__)

_PROJECT_MENTION_RE = re.compile(r'(?:^|\s)#([a-z0-9][a-z0-9-]*)', re.IGNORECASE)


def _load_config(config_path: str | Path = "config/default.toml") -> dict:
    path = Path(config_path)
    if path.exists():
        with open(path, "rb") as f:
            return tomllib.load(f)
    return {}


def _build_provider(config: dict) -> LLMProvider:
    default = config.get("provider", {}).get("default", "codex_oauth")
    providers_cfg = config.get("providers", {})
    cfg = providers_cfg.get(default, {})

    if default == "codex_oauth":
        return CodexOAuthProvider(base_url=cfg.get("base_url"))
    elif default == "openai_api":
        import os
        api_key = os.environ.get(cfg.get("env_var", "OPENAI_API_KEY"), "")
        return OpenAIAPIProvider(
            model=cfg.get("model"),
            api_key=api_key,
            base_url=cfg.get("base_url"),
        )
    elif default == "anthropic":
        import os
        from ..providers.anthropic import AnthropicProvider
        api_key = os.environ.get(cfg.get("env_var", "ANTHROPIC_API_KEY"), "")
        return AnthropicProvider(
            model=cfg.get("model"),
            api_key=api_key,
        )
    else:
        raise ValueError(f"Proveedor desconocido: {default}")


SYNTHESIS_PROMPT = """Tenés los resultados de múltiples agentes especializados trabajando en paralelo.
Sintetizá todo en UNA respuesta clara, estructurada y útil para el usuario.
No repitas contenido innecesariamente. Sé directo. Usá markdown."""


class Orchestrator:
    """
    Orquestador principal del sistema.
    Fase 2: planner multi-agente + ejecución paralela/secuencial.
    """

    def __init__(self, config_path: str | Path = "config/default.toml"):
        self.config = _load_config(config_path)
        self.provider: LLMProvider | None = None
        self.db: Database | None = None
        self.vectors: VectorMemory | None = None
        self.context_builder: ContextBuilder | None = None
        self.planner: Planner | None = None
        self.task_queue: TaskQueue | None = None
        self.agents: dict[str, Agent] = {}
        self._improvement_running: bool = False
        self._last_user_activity: float = time.time()
        self._improvement_loop: Any = None
        self._autonomous_loop: Any = None
        self._error_collector: Any = None
        self._pending_reminders: list[dict] = []
        self._last_run_tokens: int = 0

    async def start(self) -> None:
        """Inicializa todos los subsistemas."""
        mem_cfg = self.config.get("memory", {})
        db_path = mem_cfg.get("db_path", "data/memory.db")
        workspace = self.config.get("workspace", {}).get("root", "workspace")
        agent_cfg = self.config.get("agent", {})
        max_iter = agent_cfg.get("max_iterations", 20)
        api_cfg = self.config.get("api", {})
        skills_cfg = self.config.get("skills", {})

        from ..tools.filesystem import set_workspace
        set_workspace(workspace)

        self.db = Database(db_path)
        await self.db.connect()

        try:
            self.vectors = VectorMemory(db_path)
            await self.vectors.connect()
        except Exception as e:
            logger.warning("VectorMemory no disponible: %s", e)
            self.vectors = None

        self.provider = _build_provider(self.config)

        self.context_builder = ContextBuilder(
            db=self.db,
            vectors=self.vectors,
            provider=self.provider,
            recent_limit=10,
            top_k=mem_cfg.get("top_k_context", 5),
        )

        # Construir agentes especializados
        from ..agents import build_agent_registry
        self.agents = build_agent_registry(
            provider=self.provider,
            tool_registry=registry,
            max_iterations=max_iter,
        )

        # Planner y TaskQueue
        self.planner = Planner(provider=self.provider)
        self.task_queue = TaskQueue(agents=self.agents)

        # Cargar skills desde skills/
        from ..skills import load_all_skills, set_skills_dir
        skills_dir = skills_cfg.get("dir", "skills")
        set_skills_dir(skills_dir)
        loaded_skills = load_all_skills()
        if loaded_skills:
            logger.info("Skills cargados: %s", loaded_skills)

        # Git auto-init si no existe .git
        import subprocess
        if not (Path(".") / ".git").exists():
            try:
                subprocess.run(["git", "init"], capture_output=True, check=True)
                subprocess.run(["git", "add", "-A"], capture_output=True)
                subprocess.run(["git", "commit", "-m", f"chore: initial commit {SYSTEM_NAME}"], capture_output=True)
                logger.info("Git repo inicializado")
            except Exception as e:
                logger.debug("Git init omitido: %s", e)

        # Iniciar message bus
        await bus.start()

        # API HTTP en background (si está habilitada)
        if api_cfg.get("enabled", False):
            import asyncio
            from ..api import start_api_server
            asyncio.create_task(
                start_api_server(
                    self,
                    host=api_cfg.get("host", "127.0.0.1"),
                    port=api_cfg.get("port", 8765),
                )
            )

        # ── ErrorCollector: captura errores de bbclaw.* en memoria ───────────
        from .error_collector import ErrorCollector
        self._error_collector = ErrorCollector()
        logging.getLogger("bbclaw").addHandler(self._error_collector)

        # ── Auto-commit + Background loops ────────────────────────────────────
        from ..tools.registry import enable_auto_commit
        enable_auto_commit()

        imp_cfg = self.config.get("improvement", {})
        if imp_cfg.get("enabled", True):
            from .improvement_loop import ImprovementLoop
            self._improvement_loop = ImprovementLoop(self)
            await self._improvement_loop.start()

        auto_cfg = self.config.get("autonomous", {})
        if auto_cfg.get("enabled", True):
            from .autonomous_loop import AutonomousLoop
            self._autonomous_loop = AutonomousLoop(self)
            await self._autonomous_loop.start()

        logger.info(
            "Sistema %s iniciado. Agentes: %s | Workspace: %s | Skills: %s",
            SYSTEM_NAME,
            list(self.agents.keys()),
            workspace,
            loaded_skills,
        )

    async def _extract_and_switch_project(self, text: str) -> str:
        """Si el texto contiene #proyecto, cambia al proyecto y retorna texto limpio."""
        match = _PROJECT_MENTION_RE.search(text)
        if not match:
            return text

        slug = match.group(1).lower()

        if not self.db:
            return text
        project = await self.db.get_project_by_slug(slug)
        if not project:
            return text  # no existe → dejar el texto como está

        # Switch: workspace + session
        from ..tools.filesystem import set_workspace
        set_workspace(project["workspace_path"])
        await self.db.update_project_last_used(project["id"])

        from ..tools.projects import get_current_session
        session = get_current_session()
        if session is not None:
            session.active_project_id = project["id"]

        logger.info("Auto-switch a proyecto '%s' (slug: %s)", project["name"], slug)

        try:
            from ..api.server import _broadcast
            _broadcast("project_changed", {
                "projectId": project["id"],
                "projectName": project["name"],
                "projectSlug": project["slug"],
            })
        except Exception:
            pass

        # Limpiar #mención del texto
        start = match.start()
        if start < len(text) and text[start] == ' ':
            start += 1
        cleaned = (text[:start] + text[match.end():]).strip()
        return cleaned or text  # si queda vacío, devolver original

    _MULTI_STEP_KEYWORDS = (
        "y luego", "después", "primero", "paso 1", "paso 2",
        "1.", "2.", "además", "investiga y", "analiza y",
        "and then", "first", "step 1", "step 2", "also",
    )

    def _is_simple_task(self, user_input: str) -> bool:
        """Heurística: True si la tarea es lo suficientemente simple para modo directo."""
        if len(user_input) > 500:
            return False
        lower = user_input.lower()
        return not any(kw in lower for kw in self._MULTI_STEP_KEYWORDS)

    async def run_direct(self, user_input: str, memory_ctx: str = "", intent: str = "user") -> str:
        """
        Modo directo: bypasea planner y task_queue para tareas simples.
        Un solo agente resuelve directamente.
        """
        assert self.db

        agent = self.agents.get("coder") or self.agents.get("generalist")
        if not agent:
            raise RuntimeError("No hay agente disponible para modo directo")

        logger.info("Modo directo para: %s", user_input[:80])

        ctx = AgentContext(
            task_description=user_input,
            memory_context=memory_ctx,
        )
        result = await agent.run(ctx)
        self._last_run_tokens = result.tokens_used
        response = result.output if result.success else f"Error: {result.error}"

        # Guardar en memoria (misma lógica que run() pasos 5-6)
        conv_id = await self.db.save_conversation(
            user_msg=user_input,
            agent_msg=response,
            metadata={"mode": "direct", "agent": agent.name, "success": result.success},
        )

        # Task persistence (best-effort)
        try:
            import uuid as _uuid
            await self.db.upsert_task(
                task_id=f"direct-{_uuid.uuid4().hex[:8]}",
                name=user_input[:100],
                status="done" if result.success else "failed",
                agent=agent.name,
                input=user_input[:2000],
                result=(result.output or "")[:5000] if result.success else None,
                error=(result.error or "")[:2000] if not result.success else None,
            )
        except Exception:
            pass

        if self.vectors and self.provider:
            try:
                embedding = await self.provider.embed(f"{user_input}\n{response}")
                await self.vectors.store(
                    text=f"Usuario: {user_input}\nAsistente: {response[:500]}",
                    embedding=embedding,
                    metadata={"conv_id": conv_id},
                )
            except Exception as e:
                logger.debug("No se pudo guardar embedding: %s", e)

        return response

    async def run(self, user_input: str, intent: str = "user") -> str:
        """
        Pipeline completo:
        1. Construir contexto de memoria
        2. Planner genera el plan
        3. TaskQueue ejecuta (paralelo + secuencial)
        4. Sintetizar respuesta
        5. Guardar en memoria
        """
        assert self.planner and self.task_queue and self.db and self.context_builder

        if intent == "user":
            self._last_user_activity = time.time()

        # Esperar si improvement está corriendo (solo para requests de usuario)
        if intent == "user" and self._improvement_running:
            for _ in range(30):
                await asyncio.sleep(1)
                if not self._improvement_running:
                    break

        # Auto-switch proyecto si hay #mención
        if intent == "user":
            user_input = await self._extract_and_switch_project(user_input)

        # 1. Contexto de memoria
        memory_ctx = await self.context_builder.build(user_input)

        # Modo directo para tareas simples (bypasea planner + task_queue)
        if self._is_simple_task(user_input):
            return await self.run_direct(user_input, memory_ctx=memory_ctx, intent=intent)

        # 2. Crear plan
        plan = await self.planner.create_plan(user_input, context=memory_ctx)
        logger.info("Plan: '%s' (%d tareas)", plan.summary, len(plan.tasks))

        # 3. Ejecutar plan
        plan = await self.task_queue.execute(plan, memory_context=memory_ctx)
        self._last_run_tokens = self.task_queue.last_run_tokens

        # 4. Sintetizar respuesta
        response = await self._synthesize(user_input, plan)

        # 5. Guardar en memoria
        conv_id = await self.db.save_conversation(
            user_msg=user_input,
            agent_msg=response,
            metadata={
                "plan_id": plan.id,
                "tasks": len(plan.tasks),
                "success": not plan.has_failures(),
            },
        )

        if self.vectors and self.provider:
            try:
                embedding = await self.provider.embed(f"{user_input}\n{response}")
                await self.vectors.store(
                    text=f"Usuario: {user_input}\nAsistente: {response[:500]}",
                    embedding=embedding,
                    metadata={"conv_id": conv_id},
                )
            except Exception as e:
                logger.debug("No se pudo guardar embedding: %s", e)

        return response

    async def _synthesize(self, user_input: str, plan: Plan) -> str:
        """
        Si el plan tiene una sola tarea exitosa → retorna su resultado directamente.
        Si tiene múltiples → usa el agente orquestador para sintetizar.
        """
        done_tasks = [t for t in plan.tasks if t.status == "done"]
        failed_tasks = [t for t in plan.tasks if t.status == "failed"]

        # Caso simple: una sola tarea OK
        if len(plan.tasks) == 1 and done_tasks:
            return done_tasks[0].result or "(sin resultado)"

        # Construir resumen de resultados para el LLM
        results_text = ""
        for task in plan.tasks:
            if task.status == "done":
                results_text += f"### {task.name} (agente: {task.agent})\n{task.result or ''}\n\n"
            elif task.status == "failed":
                results_text += f"### {task.name} — FALLÓ\nError: {task.error}\n\n"

        if not results_text:
            return "No se obtuvieron resultados de los agentes."

        # Si no hay agente orquestador, devolver resultados crudos
        orchestrator = self.agents.get("orchestrator")
        if not orchestrator:
            return results_text

        synthesis_input = (
            f"Solicitud del usuario: {user_input}\n\n"
            f"Resultados de los agentes:\n{results_text}"
        )

        ctx = AgentContext(
            task_description=synthesis_input,
            memory_context=SYNTHESIS_PROMPT,
        )
        result = await orchestrator.run(ctx)
        return result.output if result.success else results_text

    async def stop(self) -> None:
        """Cierra todos los recursos."""
        if self._improvement_loop:
            await self._improvement_loop.stop()
        if self._autonomous_loop:
            await self._autonomous_loop.stop()
        if self._error_collector:
            logging.getLogger("bbclaw").removeHandler(self._error_collector)
            self._error_collector = None
        await bus.stop()
        if self.db:
            await self.db.close()
        if self.vectors:
            await self.vectors.close()
        if self.provider and hasattr(self.provider, "aclose"):
            await self.provider.aclose()
        logger.info("Sistema %s detenido.", SYSTEM_NAME)

    def get_and_clear_reminders(self) -> list[dict]:
        """Pop all pending reminders for display in REPL."""
        reminders = self._pending_reminders[:]
        self._pending_reminders.clear()
        return reminders
