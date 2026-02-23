"""
API HTTP interna usando FastAPI.
Expone endpoints REST + SSE para el dashboard y otras integraciones.

Rutas:
  GET  /api/health
  GET  /api/metrics
  GET  /api/metrics/business
  GET  /api/metrics/orchestrator
  GET  /api/objectives/overview
  GET  /api/objectives
  GET  /api/objectives/{id}
  GET  /api/improvement/status
  GET  /api/tasks/recent
  GET  /api/tasks/upcoming
  GET  /api/tasks/{id}
  POST /api/tasks/{id}/cancel
  GET  /api/projects
  GET  /api/task-templates/{id}
  PATCH /api/task-templates/{id}
  POST /api/task-templates/{id}/cancel-next
  POST /api/task-templates/{id}/deactivate
  POST /api/prompt
  GET  /api/chat/history
  GET  /api/active-project
  GET  /api/events  (SSE)
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

try:
    from fastapi import FastAPI, HTTPException, APIRouter
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import StreamingResponse
    from pydantic import BaseModel
    _FASTAPI_AVAILABLE = True
    class PromptRequest(BaseModel):
        message: str
        channel: str = "web"
        sessionId: str | None = None

    class TemplateUpdateRequest(BaseModel):
        title: str | None = None
        description: str | None = None
        priority: str | None = None
        timezone: str | None = None
        isActive: bool | None = None
        recurrenceRule: str | None = None

    class ChatRequest(BaseModel):
        message: str
        stream: bool = False

except ImportError:
    _FASTAPI_AVAILABLE = False
    logger.warning(
        "FastAPI no instalado — server HTTP no disponible. "
        "Instalar con: pip install fastapi uvicorn"
    )

# ── SSE broadcast ─────────────────────────────────────────────────────────────

_sse_queues: list[asyncio.Queue] = []


def _broadcast(event_type: str, payload: dict | None = None) -> None:
    """Envía un evento a todos los clientes SSE activos."""
    data = json.dumps({"type": event_type, "payload": payload or {}})
    for q in list(_sse_queues):
        try:
            q.put_nowait(data)
        except Exception:
            pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


import time as _time_mod
_start_time = _time_mod.time()


def _iso_to_epoch(iso_str: str) -> int:
    """Convierte ISO8601 string a epoch millis para el dashboard."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        return int(_time_mod.time() * 1000)


# ── App factory ───────────────────────────────────────────────────────────────

def create_app(orchestrator) -> Any:
    if not _FASTAPI_AVAILABLE:
        return None

    from bbclaw.identity import SYSTEM_NAME

    app = FastAPI(
        title=f"{SYSTEM_NAME} API",
        description="Sistema de agentes auto-mejorable — API interna",
        version="0.1.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    api = APIRouter(prefix="/api")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _db():
        if not orchestrator.db:
            raise HTTPException(status_code=503, detail="DB no disponible")
        return orchestrator.db

    async def _tasks_in_window(db, hours: int, limit: int) -> list[dict]:
        try:
            return await db.fetchall(
                "SELECT id, name, status, agent, result, error, created_at, updated_at "
                "FROM tasks "
                "WHERE updated_at >= datetime('now', ? || ' hours') "
                "ORDER BY updated_at DESC LIMIT ?",
                (f"-{hours}", limit),
            )
        except Exception:
            return []

    # ── Health ────────────────────────────────────────────────────────────────

    @api.get("/health")
    async def health():
        return {"status": "ok", "system": SYSTEM_NAME}

    # ── Metrics ───────────────────────────────────────────────────────────────

    @api.get("/metrics")
    async def metrics():
        db = _db()
        tasks = await db.get_tasks()
        counts = {"pending": 0, "running": 0, "blocked": 0, "completed": 0, "failed": 0, "canceled": 0}
        status_map = {"done": "completed", "cancelled": "canceled", "canceled": "canceled"}
        for t in tasks:
            s = t.get("status", "pending")
            s = status_map.get(s, s)
            if s in counts:
                counts[s] += 1
            else:
                counts["pending"] += 1
        projects = await db.get_all_projects()
        import time
        return {
            "tasks": counts,
            "activeAgents": 0,
            "totalAgents": 1,
            "activeProjects": len(projects),
            "recentRunsLast24h": counts["completed"] + counts["failed"],
            "uptimeSeconds": int(time.time() - _start_time),
            "timestamp": _now_iso(),
        }

    @api.get("/metrics/business")
    async def metrics_business(hours: int = 24, focus_limit: int = 10):
        db = _db()
        tasks = await _tasks_in_window(db, hours, 500)
        done = [t for t in tasks if t.get("status") == "done"]
        failed = [t for t in tasks if t.get("status") == "failed"]
        pending = [t for t in tasks if t.get("status") == "pending"]
        running = [t for t in tasks if t.get("status") == "running"]
        total = len(tasks)
        success_rate = round(len(done) / max(total, 1) * 100, 1)
        return {
            "windowHours": hours,
            "timestamp": _now_iso(),
            "throughput": {
                "completedTasks": len(done),
                "completedObjectives": 0,
                "avgLeadTimeHours": None,
            },
            "reliability": {
                "runSuccessRatePct": success_rate,
                "firstPassRunSuccessRatePct": success_rate,
                "failedRuns": len(failed),
                "retriedRuns": 0,
            },
            "flow": {
                "pendingTasks": len(pending),
                "runningTasks": len(running),
                "blockedTasks": 0,
                "blockedWorkRatioPct": 0,
                "overdueTasks": 0,
                "dueSoonTasks": 0,
                "pendingAgeP95Minutes": 0,
            },
            "capacity": {
                "activeAgents": 0,
                "runningTaskRuns": len(running),
                "maxParallelTaskRuns": 5,
                "maxTaskRunsPerAgent": 5,
                "effectiveCapacity": 5,
                "utilizationPct": 0,
            },
            "economics": {
                "llmCostUsd": 0,
                "llmTotalTokens": 0,
                "costPerCompletedTaskUsd": None,
                "costPerSuccessfulRunUsd": None,
            },
            "risks": [],
            "focus": {
                "blockedTasks": [],
                "overdueTasks": [],
                "failingTasks": [],
            },
            "projects": [],
        }

    @api.get("/metrics/orchestrator")
    async def metrics_orchestrator(hours: int = 24):
        _zero_group = {"key": "all", "count": 0, "p50TotalMs": 0, "p95TotalMs": 0, "p95IntentGateMs": 0, "p95SyncLlmMs": 0, "slaExceededRate": 0}
        return {
            "windowHours": hours,
            "generatedAtIso": _now_iso(),
            "totalEvents": 0,
            "overall": _zero_group,
            "byChannel": [],
            "byMode": [],
            "laneRates": {"gateTimeoutRate": 0, "syncAnswerRate": 0, "asyncFallbackRate": 0},
        }

    # ── Objectives ────────────────────────────────────────────────────────────

    @api.get("/objectives/overview")
    async def objectives_overview(hours: int = 24, limit: int = 10):
        return {
            "windowHours": hours,
            "timestamp": _now_iso(),
            "objectives": {
                "total": 0, "active": 0, "blocked": 0,
                "completedLastWindow": 0, "failedLastWindow": 0, "canceled": 0,
            },
            "steps": {
                "pending": 0, "ready": 0, "queued": 0, "running": 0,
                "blocked": 0, "completed": 0, "failed": 0, "canceled": 0,
                "inFlightNonVerify": 0, "readyNonVerify": 0,
                "maxParallelBudgetActiveObjectives": 0, "parallelUtilizationPct": 0,
            },
            "blockedSteps": [],
            "failingSteps": [],
        }

    @api.get("/objectives")
    async def objectives(limit: int = 30):
        return []

    @api.get("/objectives/{objective_id}")
    async def objective_detail(objective_id: str):
        raise HTTPException(status_code=404, detail="Objectives no disponibles en esta versión")

    # ── Improvement ───────────────────────────────────────────────────────────

    @api.get("/improvement/status")
    async def improvement_status():
        imp = getattr(orchestrator, "_improvement_loop", None)
        auto = getattr(orchestrator, "_autonomous_loop", None)

        # Obtener conteo real de objectives activos
        active_objectives = 0
        try:
            db = _db()
            objs = await db.get_objectives(status="active")
            active_objectives = len(objs)
        except Exception:
            pass

        return {
            "improvementLoop": imp.status if imp else {
                "isRunning": False,
                "cycleCount": 0,
                "consecutiveNoImprovement": 0,
                "lastRunAt": None,
                "lastScoreDelta": None,
                "tokensLastHour": 0,
                "tokenBudget": 80000,
            },
            "autonomousLoop": {
                **(auto.status if auto else {
                    "isRunning": False,
                    "currentObjective": None,
                }),
                "activeObjectives": active_objectives,
            },
            "behavioralSuite": {
                "lastScore": 0,
                "casesPassed": 0,
                "casesTotal": 0,
            },
            "providers": [],
        }

    # ── Tasks ─────────────────────────────────────────────────────────────────

    @api.get("/tasks/recent")
    async def tasks_recent(hours: int = 24, limit: int = 100):
        db = _db()
        raw = await _tasks_in_window(db, hours, limit)
        status_map = {"done": "completed", "cancelled": "canceled"}
        items = []
        for t in raw:
            s = t.get("status", "pending")
            created = t.get("created_at", _now_iso())
            updated = t.get("updated_at", created)
            items.append({
                "id": t.get("id", ""),
                "title": t.get("name", "Sin título"),
                "status": status_map.get(s, s),
                "priority": 3,
                "projectId": "",
                "createdAt": _iso_to_epoch(created),
                "updatedAt": _iso_to_epoch(updated),
            })
        return items

    @api.get("/tasks/upcoming")
    async def tasks_upcoming(awaiting_limit: int = 120, scheduled_limit: int = 120):
        db = _db()
        # Pending tasks awaiting execution
        awaiting = []
        try:
            pending = await db.fetchall(
                "SELECT id, name, status, created_at FROM tasks "
                "WHERE status = 'pending' ORDER BY created_at ASC LIMIT ?",
                (awaiting_limit,),
            )
            for t in pending:
                awaiting.append({
                    "id": t["id"],
                    "title": t.get("name", "Sin título"),
                    "type": "task",
                    "status": t.get("status", "pending"),
                    "createdAt": t.get("created_at", ""),
                })
        except Exception:
            pass
        # Scheduled items
        scheduled = []
        try:
            items = await db.get_scheduled_items(status="active")
            from bbclaw.core.scheduler import describe_schedule
            for item in items[:scheduled_limit]:
                sched = item["schedule"] if isinstance(item["schedule"], dict) else {}
                scheduled.append({
                    "id": item["id"],
                    "title": item["title"],
                    "type": item.get("item_type", "task"),
                    "status": item.get("status", "active"),
                    "nextRunAt": item.get("next_run_at"),
                    "runCount": item.get("run_count", 0),
                    "schedule": describe_schedule(sched),
                })
        except Exception:
            pass
        return {"awaitingNow": awaiting, "scheduled": scheduled}

    @api.get("/tasks/{task_id}")
    async def task_detail(task_id: str):
        db = _db()
        task = await db.fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not task:
            raise HTTPException(status_code=404, detail="Tarea no encontrada")
        status_map = {"done": "completed", "cancelled": "canceled"}
        s = task.get("status", "pending")
        created = task.get("created_at", _now_iso())
        updated = task.get("updated_at", created)
        return {
            "id": task.get("id", task_id),
            "title": task.get("name", "Sin título"),
            "status": status_map.get(s, s),
            "priority": 3,
            "projectId": "",
            "createdAt": _iso_to_epoch(created),
            "updatedAt": _iso_to_epoch(updated),
            "description": task.get("result", "") or "",
            "runHistory": [],
        }

    @api.post("/tasks/{task_id}/cancel")
    async def task_cancel(task_id: str):
        db = _db()
        task = await db.fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not task:
            raise HTTPException(status_code=404, detail="Tarea no encontrada")
        if task.get("status") not in ("pending", "running"):
            return {"ok": False, "message": f"No se puede cancelar tarea en estado '{task.get('status')}'"}
        await db.execute(
            "UPDATE tasks SET status = 'cancelled', "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
            "WHERE id = ?",
            (task_id,),
        )
        return {"ok": True, "message": "Tarea cancelada"}

    # ── Projects ──────────────────────────────────────────────────────────────

    @api.get("/projects")
    async def projects():
        db = _db()
        raw = await db.get_all_projects()
        items = []
        for p in raw:
            items.append({
                "id": p.get("id", ""),
                "name": p.get("name", ""),
                "slug": p.get("slug", ""),
                "status": "active",
                "taskCounts": {"pending": 0, "running": 0, "blocked": 0, "completed": 0, "failed": 0, "canceled": 0},
            })
        return items

    # ── Active project ─────────────────────────────────────────────────────────

    @api.get("/active-project")
    async def active_project():
        from bbclaw.tools.projects import _current_session
        if _current_session and getattr(_current_session, "active_project_id", None):
            db = _db()
            project = await db.fetchone(
                "SELECT id, name, slug FROM projects WHERE id = ?",
                (_current_session.active_project_id,),
            )
            if project:
                return {"id": project["id"], "name": project["name"], "slug": project["slug"]}
        return {"id": None, "name": None, "slug": None}

    # ── Task Templates (stub) ─────────────────────────────────────────────────

    @api.get("/task-templates/{template_id}")
    async def template_detail(template_id: str):
        raise HTTPException(status_code=404, detail="Templates no disponibles en esta versión")

    @api.patch("/task-templates/{template_id}")
    async def template_update(template_id: str, body: TemplateUpdateRequest):
        raise HTTPException(status_code=404, detail="Templates no disponibles en esta versión")

    @api.post("/task-templates/{template_id}/cancel-next")
    async def template_cancel_next(template_id: str):
        raise HTTPException(status_code=404, detail="Templates no disponibles en esta versión")

    @api.post("/task-templates/{template_id}/deactivate")
    async def template_deactivate(template_id: str):
        raise HTTPException(status_code=404, detail="Templates no disponibles en esta versión")

    # ── Prompt ────────────────────────────────────────────────────────────────

    @api.post("/prompt")
    async def prompt(req: PromptRequest):
        request_id = str(uuid.uuid4())
        try:
            response = await orchestrator.run(req.message)
            _broadcast("request_finalized", {
                "requestId": request_id,
                "message": response[:200],
            })
            return {
                "humanMessage": response,
                "message": response,
                "requestId": request_id,
                "sessionId": req.sessionId or request_id,
                "outcome": "completed",
            }
        except Exception as e:
            logger.error("Error en /api/prompt: %s", e)
            _broadcast("request_failed", {"requestId": request_id, "message": str(e)})
            error_msg = f"Error: {e}"
            return {
                "humanMessage": error_msg,
                "message": error_msg,
                "requestId": request_id,
                "sessionId": req.sessionId or request_id,
                "outcome": "error",
            }

    # ── Chat history ──────────────────────────────────────────────────────────

    @api.get("/chat/history")
    async def chat_history(
        channel: str = "web",
        limit: int = 30,
        include_previous_sessions: int = 0,
    ):
        db = _db()
        convs = await db.get_recent_conversations(limit)
        messages = []
        for c in reversed(convs):
            ts_iso = c.get("timestamp", _now_iso())
            ts_epoch = _iso_to_epoch(ts_iso)
            conv_id = c.get("id", "")
            messages.append({
                "id": f"{conv_id}:user",
                "role": "user",
                "text": c["user_msg"],
                "createdAt": ts_epoch,
            })
            if c.get("agent_msg"):
                messages.append({
                    "id": f"{conv_id}:system",
                    "role": "system",
                    "text": c["agent_msg"],
                    "createdAt": ts_epoch + 1,
                })
        return {"messages": messages, "sessionId": channel}

    # ── SSE events ────────────────────────────────────────────────────────────

    @api.get("/events")
    async def events():
        q: asyncio.Queue = asyncio.Queue()
        _sse_queues.append(q)

        async def generate():
            try:
                while True:
                    try:
                        data = await asyncio.wait_for(q.get(), timeout=15.0)
                        yield f"data: {data}\n\n"
                    except asyncio.TimeoutError:
                        yield f"data: {json.dumps({'type': 'heartbeat', 'timestamp': _now_iso()})}\n\n"
            except (asyncio.CancelledError, GeneratorExit):
                pass
            finally:
                if q in _sse_queues:
                    _sse_queues.remove(q)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # ── Legacy root endpoints (compatibilidad hacia atrás) ────────────────────

    @app.get("/health")
    async def health_legacy():
        return {"status": "ok", "system": SYSTEM_NAME}

    @app.get("/metrics")
    async def metrics_legacy():
        return await metrics()

    @app.post("/chat")
    async def chat_legacy(req: ChatRequest):
        request_id = str(uuid.uuid4())
        try:
            response = await orchestrator.run(req.message)
            return {"response": response, "requestId": request_id}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/history")
    async def history_legacy(limit: int = 20):
        db = _db()
        return {"conversations": await db.get_recent_conversations(limit)}

    @app.get("/tools")
    async def tools_legacy():
        from bbclaw.tools.registry import registry
        return {"tools": registry.list_tools()}

    # ── Mount /api router ─────────────────────────────────────────────────────

    app.include_router(api)

    # ── Servir dashboard estático si está configurado ─────────────────────────

    dash_dist = orchestrator.config.get("api", {}).get("dashboard_dist", "")
    if dash_dist:
        from pathlib import Path
        from fastapi.staticfiles import StaticFiles
        dist_path = Path(dash_dist)
        if dist_path.exists():
            app.mount("/", StaticFiles(directory=str(dist_path), html=True), name="dashboard")
            logger.info("Dashboard disponible en http://127.0.0.1:%d/",
                        orchestrator.config.get("api", {}).get("port", 8765))
        else:
            logger.warning("dashboard_dist configurado pero no existe: %s", dash_dist)

    return app


async def start_api_server(orchestrator, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Inicia el servidor FastAPI en background (como asyncio task)."""
    if not _FASTAPI_AVAILABLE:
        logger.warning("FastAPI no instalado — server HTTP no iniciado")
        return

    import uvicorn

    app = create_app(orchestrator)
    if app is None:
        return

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    logger.info("API HTTP disponible en http://%s:%d/api/", host, port)
    await server.serve()
