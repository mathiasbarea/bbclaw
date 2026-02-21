"""
Orquestador principal del sistema de agentes — Fase 2.
Pipeline completo: contexto → planner → task_queue → síntesis → memoria.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path

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

logger = logging.getLogger(__name__)


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
                subprocess.run(["git", "commit", "-m", "chore: initial commit bbclaud"], capture_output=True)
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

        logger.info(
            "Sistema bbclaud iniciado. Agentes: %s | Workspace: %s | Skills: %s",
            list(self.agents.keys()),
            workspace,
            loaded_skills,
        )

    async def run(self, user_input: str) -> str:
        """
        Pipeline completo:
        1. Construir contexto de memoria
        2. Planner genera el plan
        3. TaskQueue ejecuta (paralelo + secuencial)
        4. Sintetizar respuesta
        5. Guardar en memoria
        """
        assert self.planner and self.task_queue and self.db and self.context_builder

        # 1. Contexto de memoria
        memory_ctx = await self.context_builder.build(user_input)

        # 2. Crear plan
        plan = await self.planner.create_plan(user_input, context=memory_ctx)
        logger.info("Plan: '%s' (%d tareas)", plan.summary, len(plan.tasks))

        # 3. Ejecutar plan
        plan = await self.task_queue.execute(plan)

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
        await bus.stop()
        if self.db:
            await self.db.close()
        if self.vectors:
            await self.vectors.close()
        if self.provider and hasattr(self.provider, "aclose"):
            await self.provider.aclose()
        logger.info("Sistema bbclaud detenido.")
