"""
Herramientas de scheduling para agentes.
Permite crear, listar y gestionar tareas programadas y reminders.
"""

from __future__ import annotations

import json
import logging
import uuid

from .registry import registry

logger = logging.getLogger(__name__)


def _gen_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@registry.tool(
    name="create_scheduled_task",
    description=(
        "Crea una tarea programada que se ejecutará automáticamente según el schedule. "
        "Schedule spec: {\"type\": \"once|interval|daily|weekly|monthly\", ...}. "
        "Ejemplos: {\"type\": \"daily\", \"time\": \"09:00\"}, "
        "{\"type\": \"interval\", \"minutes\": 30}, "
        "{\"type\": \"weekly\", \"day\": \"monday\", \"time\": \"10:00\"}."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Título de la tarea programada.",
            },
            "description": {
                "type": "string",
                "description": "Descripción detallada de lo que debe hacer la tarea.",
            },
            "schedule": {
                "type": "object",
                "description": "Schedule spec JSON con tipo y parámetros.",
            },
        },
        "required": ["title", "description", "schedule"],
    },
)
async def create_scheduled_task(title: str, description: str, schedule: dict) -> str:
    from bbclaw.memory.db import get_db
    from bbclaw.core.scheduler import parse_schedule, compute_next_run

    try:
        parse_schedule(schedule)
    except ValueError as e:
        return f"Error en schedule: {e}"

    next_run = compute_next_run(schedule)
    if next_run is None:
        return "Error: el schedule no tiene ejecuciones futuras (ya pasó)."

    item_id = _gen_id("sched")
    db = get_db()
    await db.create_scheduled_item(
        item_id=item_id,
        item_type="task",
        title=title,
        description=description,
        schedule=json.dumps(schedule),
        next_run_at=next_run,
    )
    from bbclaw.core.scheduler import describe_schedule
    return (
        f"Tarea programada creada: {item_id}\n"
        f"  Título: {title}\n"
        f"  Schedule: {describe_schedule(schedule)}\n"
        f"  Próxima ejecución: {next_run}"
    )


@registry.tool(
    name="create_reminder",
    description=(
        "Crea un reminder que se mostrará al usuario (NO ejecuta agente). "
        "Schedule spec igual que create_scheduled_task."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Texto del recordatorio.",
            },
            "schedule": {
                "type": "object",
                "description": "Schedule spec JSON.",
            },
        },
        "required": ["title", "schedule"],
    },
)
async def create_reminder(title: str, schedule: dict) -> str:
    from bbclaw.memory.db import get_db
    from bbclaw.core.scheduler import parse_schedule, compute_next_run, describe_schedule

    try:
        parse_schedule(schedule)
    except ValueError as e:
        return f"Error en schedule: {e}"

    next_run = compute_next_run(schedule)
    if next_run is None:
        return "Error: el schedule no tiene ejecuciones futuras (ya pasó)."

    item_id = _gen_id("rem")
    db = get_db()
    await db.create_scheduled_item(
        item_id=item_id,
        item_type="reminder",
        title=title,
        description="",
        schedule=json.dumps(schedule),
        next_run_at=next_run,
    )
    return (
        f"Reminder creado: {item_id}\n"
        f"  Texto: {title}\n"
        f"  Schedule: {describe_schedule(schedule)}\n"
        f"  Próxima ejecución: {next_run}"
    )


@registry.tool(
    name="list_scheduled_items",
    description="Lista tareas programadas y reminders. Filtro opcional por status (active, paused, done, cancelled).",
    parameters={
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "description": "Filtrar por status: active, paused, done, cancelled. Si se omite muestra todos.",
            },
        },
        "required": [],
    },
)
async def list_scheduled_items(status: str = "") -> str:
    from bbclaw.memory.db import get_db
    from bbclaw.core.scheduler import describe_schedule

    db = get_db()
    items = await db.get_scheduled_items(status=status or None)
    if not items:
        return "No hay items programados" + (f" con status '{status}'" if status else "") + "."

    lines = [f"Items programados ({len(items)}):\n"]
    for item in items:
        sched = item["schedule"] if isinstance(item["schedule"], dict) else {}
        icon = "🔔" if item["item_type"] == "reminder" else "📋"
        status_icon = {"active": "🟢", "paused": "⏸️", "done": "✅", "cancelled": "❌"}.get(item["status"], "⚪")
        lines.append(
            f"  {icon} {status_icon} {item['id']} — {item['title']}\n"
            f"    Schedule: {describe_schedule(sched)}\n"
            f"    Próxima: {item.get('next_run_at', 'N/A')} | Ejecuciones: {item.get('run_count', 0)}"
        )
    return "\n".join(lines)


@registry.tool(
    name="cancel_scheduled_item",
    description="Cancela un item programado (tarea o reminder).",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "ID del item a cancelar (sched-xxx o rem-xxx).",
            },
        },
        "required": ["item_id"],
    },
)
async def cancel_scheduled_item(item_id: str) -> str:
    from bbclaw.memory.db import get_db
    db = get_db()
    item = await db.get_scheduled_item(item_id)
    if not item:
        return f"Item no encontrado: {item_id}"
    if item["status"] == "cancelled":
        return f"Item {item_id} ya estaba cancelado."
    await db.update_scheduled_item(item_id, status="cancelled", next_run_at=None)
    return f"Item cancelado: {item_id} ({item['title']})"


@registry.tool(
    name="pause_scheduled_item",
    description="Pausa un item programado. No se ejecutará hasta que se resuma.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "ID del item a pausar.",
            },
        },
        "required": ["item_id"],
    },
)
async def pause_scheduled_item(item_id: str) -> str:
    from bbclaw.memory.db import get_db
    db = get_db()
    item = await db.get_scheduled_item(item_id)
    if not item:
        return f"Item no encontrado: {item_id}"
    if item["status"] != "active":
        return f"Solo se puede pausar items activos. Status actual: {item['status']}"
    await db.update_scheduled_item(item_id, status="paused")
    return f"Item pausado: {item_id} ({item['title']})"


@registry.tool(
    name="resume_scheduled_item",
    description="Resume un item pausado. Recalcula la próxima ejecución.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "ID del item a resumir.",
            },
        },
        "required": ["item_id"],
    },
)
async def resume_scheduled_item(item_id: str) -> str:
    from bbclaw.memory.db import get_db
    from bbclaw.core.scheduler import compute_next_run

    db = get_db()
    item = await db.get_scheduled_item(item_id)
    if not item:
        return f"Item no encontrado: {item_id}"
    if item["status"] != "paused":
        return f"Solo se puede resumir items pausados. Status actual: {item['status']}"

    sched = item["schedule"] if isinstance(item["schedule"], dict) else {}
    next_run = compute_next_run(sched)
    if next_run is None:
        await db.update_scheduled_item(item_id, status="done", next_run_at=None)
        return f"Item {item_id} no tiene más ejecuciones futuras. Marcado como done."

    await db.update_scheduled_item(item_id, status="active", next_run_at=next_run)
    return f"Item resumido: {item_id} ({item['title']})\nPróxima ejecución: {next_run}"


@registry.tool(
    name="get_scheduled_item",
    description="Muestra detalle completo de un item programado.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "ID del item.",
            },
        },
        "required": ["item_id"],
    },
)
async def get_scheduled_item(item_id: str) -> str:
    from bbclaw.memory.db import get_db
    from bbclaw.core.scheduler import describe_schedule

    db = get_db()
    item = await db.get_scheduled_item(item_id)
    if not item:
        return f"Item no encontrado: {item_id}"

    sched = item["schedule"] if isinstance(item["schedule"], dict) else {}
    icon = "🔔" if item["item_type"] == "reminder" else "📋"
    return (
        f"{icon} {item['id']}\n"
        f"  Tipo: {item['item_type']}\n"
        f"  Título: {item['title']}\n"
        f"  Descripción: {item.get('description', '')}\n"
        f"  Schedule: {describe_schedule(sched)}\n"
        f"  Status: {item['status']}\n"
        f"  Próxima ejecución: {item.get('next_run_at', 'N/A')}\n"
        f"  Última ejecución: {item.get('last_run_at', 'nunca')}\n"
        f"  Ejecuciones: {item.get('run_count', 0)}\n"
        f"  Creado: {item.get('created_at', '?')}"
    )
