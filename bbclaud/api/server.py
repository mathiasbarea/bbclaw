"""
API HTTP interna usando FastAPI.
Expone endpoints REST para interactuar con el sistema de agentes
de forma programática (integración con otras apps, UIs, etc).

Endpoints:
  POST /chat          — envía un mensaje al orquestador
  GET  /history       — últimas N conversaciones
  GET  /tools         — herramientas disponibles
  GET  /skills        — skills cargados
  GET  /agents        — agentes disponibles
  GET  /metrics       — métricas de uso
  GET  /health        — health check
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False
    logger.warning("FastAPI no está instalado. La API HTTP no estará disponible. "
                   "Instala con: pip install fastapi uvicorn")


def create_app(orchestrator) -> Any:
    """
    Crea la aplicación FastAPI con el orquestador inyectado.
    Retorna None si FastAPI no está instalado.
    """
    if not _FASTAPI_AVAILABLE:
        return None

    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel

    app = FastAPI(
        title="bbclaud API",
        description="Sistema de agentes auto-mejorable — API HTTP interna",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Schemas ──────────────────────────────────────────────────────────────

    class ChatRequest(BaseModel):
        message: str
        stream: bool = False

    class ChatResponse(BaseModel):
        response: str
        plan_tasks: int = 0

    # ── Endpoints ─────────────────────────────────────────────────────────────

    @app.get("/health")
    async def health():
        return {"status": "ok", "system": "bbclaud"}

    @app.post("/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest):
        try:
            response = await orchestrator.run(req.message)
            return ChatResponse(response=response)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/history")
    async def history(limit: int = 20):
        if not orchestrator.db:
            raise HTTPException(status_code=503, detail="DB no disponible")
        convs = await orchestrator.db.get_recent_conversations(limit)
        return {"conversations": convs}

    @app.get("/tools")
    async def tools():
        from bbclaud.tools.registry import registry
        return {
            "tools": [
                {"name": name, "schema": schema}
                for name, schema in zip(
                    registry.list_tools(),
                    registry.get_schemas(),
                )
            ]
        }

    @app.get("/skills")
    async def skills():
        from bbclaud.skills import list_loaded_skills
        return {"skills": list_loaded_skills()}

    @app.get("/agents")
    async def agents():
        return {
            "agents": [
                {"name": name, "description": agent.description}
                for name, agent in orchestrator.agents.items()
            ]
        }

    @app.get("/metrics")
    async def metrics():
        if not orchestrator.db:
            raise HTTPException(status_code=503, detail="DB no disponible")
        tasks = await orchestrator.db.get_tasks()
        total = len(tasks)
        done = sum(1 for t in tasks if t.get("status") == "done")
        failed = sum(1 for t in tasks if t.get("status") == "failed")
        return {
            "total_tasks": total,
            "done": done,
            "failed": failed,
            "success_rate": round(done / max(total, 1) * 100, 1),
        }

    @app.post("/skills/reload")
    async def reload_skills():
        from bbclaud.skills import load_all_skills
        loaded = load_all_skills()
        return {"reloaded": loaded}

    return app


async def start_api_server(orchestrator, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Inicia el servidor FastAPI en background."""
    if not _FASTAPI_AVAILABLE:
        logger.warning("FastAPI no instalado — server HTTP no iniciado")
        return

    import uvicorn

    app = create_app(orchestrator)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    logger.info("API HTTP disponible en http://%s:%d", host, port)
    await server.serve()
