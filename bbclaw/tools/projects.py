"""
Herramientas de gestión de proyectos para agentes.
Permite que el agente liste, cambie, cree, edite y elimine proyectos
sin necesidad de usar comandos /project del REPL.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from contextvars import ContextVar
from pathlib import Path

from .registry import registry

logger = logging.getLogger(__name__)

# ── Estado per-context (aislado por asyncio Task) ────────────────────────────

_session_var: ContextVar = ContextVar("current_session", default=None)


def set_current_session(session) -> None:
    """Inyecta la sesión activa en el contexto async actual."""
    _session_var.set(session)


def get_current_session():
    """Retorna la sesión del contexto async actual (o None)."""
    return _session_var.get()


# ── Helpers ───────────────────────────────────────────────────────────────────


def generate_slug(name: str) -> str:
    """Convierte un nombre de proyecto a slug URL-amigable."""
    slug = name.lower().replace(" ", "-").replace("_", "-")
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    return slug


async def _find_project(name_or_slug: str) -> tuple[dict | None, list[dict]]:
    """
    Busca un proyecto por slug exacto, luego por nombre/slug parcial.
    Retorna (project_or_None, candidates).
    """
    from bbclaw.memory.db import get_db
    db = get_db()

    # 1. Slug exacto
    project = await db.get_project_by_slug(name_or_slug.lower())
    if project:
        return project, []

    # 2. Fuzzy: nombre o slug contiene el término
    term = name_or_slug.lower()
    all_projects = await db.get_all_projects()
    candidates = [
        p for p in all_projects
        if term in p["name"].lower() or term in p["slug"]
    ]
    if len(candidates) == 1:
        return candidates[0], []
    return None, candidates


# ── Herramientas ──────────────────────────────────────────────────────────────


@registry.tool(
    name="list_projects",
    description="Lista todos los proyectos disponibles. Muestra cuál está activo actualmente.",
    parameters={"type": "object", "properties": {}, "required": []},
)
async def list_projects() -> str:
    from bbclaw.memory.db import get_db
    db = get_db()

    projects = await db.get_all_projects()
    if not projects:
        return "No hay proyectos creados. Usa create_project para crear uno."

    active_id = getattr(get_current_session(), "active_project_id", None)
    lines = ["Proyectos disponibles:\n"]
    for p in projects:
        active_mark = " [ACTIVO]" if p["id"] == active_id else ""
        desc = (p.get("description") or "")[:60]
        obj = (p.get("objective") or "")[:80]
        lines.append(
            f"  • {p['name']}{active_mark}\n"
            f"    slug: {p['slug']} | workspace: {p['workspace_path']}"
            + (f"\n    {desc}" if desc else "")
            + (f"\n    🎯 Objective: {obj}" if obj else "")
        )
    return "\n".join(lines)


@registry.tool(
    name="switch_project",
    description=(
        "Cambia el proyecto activo. Acepta nombre o slug del proyecto. "
        "Actualiza el workspace de trabajo y la sesión activa."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name_or_slug": {
                "type": "string",
                "description": "Nombre o slug del proyecto al que cambiar.",
            }
        },
        "required": ["name_or_slug"],
    },
)
async def switch_project(name_or_slug: str) -> str:
    from bbclaw.memory.db import get_db
    from bbclaw.tools.filesystem import set_workspace

    db = get_db()
    project, candidates = await _find_project(name_or_slug)

    if project is None:
        if candidates:
            slugs = ", ".join(p["slug"] for p in candidates)
            return f"Múltiples proyectos coinciden: {slugs}. Sé más específico."
        return f"Proyecto no encontrado: '{name_or_slug}'. Usa list_projects para ver los disponibles."

    set_workspace(project["workspace_path"])
    await db.update_project_last_used(project["id"])

    session = get_current_session()
    if session is not None:
        session.active_project_id = project["id"]
        try:
            await db.update_session(
                session_id=session.session_id,
                summary=session.summary,
                history_json=json.dumps(session.history, ensure_ascii=False),
                last_activity_at=session.last_activity_at,
                active_project_id=project["id"],
            )
        except Exception as e:
            logger.warning("No se pudo persistir active_project_id en DB: %s", e)

    try:
        from bbclaw.api.server import _broadcast
        _broadcast("project_changed", {
            "projectId": project["id"],
            "projectName": project["name"],
            "projectSlug": project["slug"],
        })
    except Exception:
        pass

    desc = project.get("description") or ""
    return (
        f"Proyecto activo: {project['name']} (slug: {project['slug']})\n"
        f"Workspace: {project['workspace_path']}"
        + (f"\n{desc}" if desc else "")
    )


@registry.tool(
    name="create_project",
    description=(
        "Crea un nuevo proyecto. "
        "'name' es el nombre visible. "
        "'workspace_path' debe ser la ruta ABSOLUTA al directorio raíz del proyecto "
        "(ej: C:\\\\Users\\\\mathi\\\\Documents\\\\mi-proyecto). "
        "Si se omite, se crea una subcarpeta dentro del workspace actual."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Nombre visible del proyecto.",
            },
            "description": {
                "type": "string",
                "description": "Descripción opcional del proyecto.",
            },
            "workspace_path": {
                "type": "string",
                "description": (
                    "Ruta ABSOLUTA al directorio del proyecto. "
                    "Si se omite, se usa <workspace_actual>/<slug>."
                ),
            },
        },
        "required": ["name"],
    },
)
async def create_project(
    name: str,
    description: str = "",
    workspace_path: str = "",
) -> str:
    from bbclaw.memory.db import get_db
    from bbclaw.tools.filesystem import get_workspace_root
    db = get_db()

    if not name.strip():
        return "Error: el nombre del proyecto no puede estar vacío."

    slug = generate_slug(name.strip())
    if not slug:
        return f"Error: el nombre '{name}' produce un slug vacío (usa caracteres alfanuméricos)."

    existing = await db.get_project_by_slug(slug)
    if existing:
        return f"Error: ya existe un proyecto con el slug '{slug}' (nombre: {existing['name']})."

    # Resolver workspace_path
    if workspace_path:
        # Usar el path provisto tal cual (debe ser absoluto)
        wp = str(Path(workspace_path).expanduser().resolve())
    else:
        # Subdirectorio del workspace activo actual — NO relativo al CWD del proceso
        wp = str(get_workspace_root() / slug)

    try:
        Path(wp).mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning("No se pudo crear el directorio workspace '%s': %s", wp, e)

    project_id = str(uuid.uuid4())
    try:
        await db.create_project(
            project_id=project_id,
            name=name.strip(),
            slug=slug,
            description=description,
            workspace_path=wp,
        )
    except Exception as e:
        return f"Error al crear el proyecto: {e}"

    return (
        f"Proyecto '{name}' creado.\n"
        f"  slug: {slug}\n"
        f"  workspace: {wp}\n"
        f"Usa switch_project('{slug}') para activarlo."
    )


@registry.tool(
    name="edit_project",
    description=(
        "Edita el nombre o descripción de un proyecto existente. "
        "Identifica el proyecto por nombre o slug."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name_or_slug": {
                "type": "string",
                "description": "Nombre o slug del proyecto a editar.",
            },
            "new_name": {
                "type": "string",
                "description": "Nuevo nombre del proyecto (opcional).",
            },
            "new_description": {
                "type": "string",
                "description": "Nueva descripción del proyecto (opcional).",
            },
        },
        "required": ["name_or_slug"],
    },
)
async def edit_project(
    name_or_slug: str,
    new_name: str = "",
    new_description: str = "",
) -> str:
    from bbclaw.memory.db import get_db
    db = get_db()

    project, candidates = await _find_project(name_or_slug)
    if project is None:
        if candidates:
            slugs = ", ".join(p["slug"] for p in candidates)
            return f"Múltiples proyectos coinciden: {slugs}. Sé más específico."
        return f"Proyecto no encontrado: '{name_or_slug}'."

    if not new_name and not new_description:
        return "Nada que actualizar: proporciona new_name y/o new_description."

    new_slug: str | None = None
    if new_name:
        new_slug = generate_slug(new_name.strip())
        if not new_slug:
            return f"Error: el nuevo nombre '{new_name}' produce un slug vacío."
        if new_slug != project["slug"]:
            existing = await db.get_project_by_slug(new_slug)
            if existing:
                return f"Error: ya existe un proyecto con el slug '{new_slug}'."

    await db.update_project(
        project_id=project["id"],
        name=new_name.strip() if new_name else None,
        slug=new_slug,
        description=new_description if new_description else None,
    )

    changes = []
    if new_name:
        changes.append(f"nombre → '{new_name}'")
    if new_description:
        changes.append(f"descripción → '{new_description}'")

    return f"Proyecto '{project['name']}' actualizado: {', '.join(changes)}."


@registry.tool(
    name="delete_project",
    description=(
        "Elimina un proyecto de la base de datos. "
        "El directorio de workspace NO se borra. "
        "Si era el proyecto activo, el workspace vuelve al directorio base."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name_or_slug": {
                "type": "string",
                "description": "Nombre o slug del proyecto a eliminar.",
            }
        },
        "required": ["name_or_slug"],
    },
)
async def delete_project(name_or_slug: str) -> str:
    from bbclaw.memory.db import get_db
    from bbclaw.tools.filesystem import set_workspace

    db = get_db()
    project, candidates = await _find_project(name_or_slug)
    if project is None:
        if candidates:
            slugs = ", ".join(p["slug"] for p in candidates)
            return f"Múltiples proyectos coinciden: {slugs}. Sé más específico."
        return f"Proyecto no encontrado: '{name_or_slug}'."

    await db.delete_project(project["id"])

    session = get_current_session()
    if session is not None and getattr(session, "active_project_id", None) == project["id"]:
        session.active_project_id = None
        set_workspace("workspace")
        try:
            await db.update_session(
                session_id=session.session_id,
                summary=session.summary,
                history_json=json.dumps(session.history, ensure_ascii=False),
                last_activity_at=session.last_activity_at,
                active_project_id=None,
            )
        except Exception as e:
            logger.warning("No se pudo resetear active_project_id en sesión: %s", e)

    return (
        f"Proyecto '{project['name']}' eliminado.\n"
        f"Nota: el directorio '{project['workspace_path']}' no fue borrado."
    )


@registry.tool(
    name="set_project_objective",
    description=(
        "Define o actualiza el objective de un proyecto. "
        "Si no se especifica proyecto, usa el proyecto activo."
    ),
    parameters={
        "type": "object",
        "properties": {
            "objective": {
                "type": "string",
                "description": "Texto del objetivo a definir. Vacío para limpiar.",
            },
            "name_or_slug": {
                "type": "string",
                "description": "Nombre o slug del proyecto (opcional, usa el activo si se omite).",
            },
        },
        "required": ["objective"],
    },
)
async def set_project_objective(objective: str, name_or_slug: str = "") -> str:
    from bbclaw.memory.db import get_db
    db = get_db()

    if name_or_slug:
        project, candidates = await _find_project(name_or_slug)
        if project is None:
            if candidates:
                slugs = ", ".join(p["slug"] for p in candidates)
                return f"Múltiples proyectos coinciden: {slugs}. Sé más específico."
            return f"Proyecto no encontrado: '{name_or_slug}'."
    else:
        active_id = getattr(get_current_session(), "active_project_id", None)
        if not active_id:
            return "No hay proyecto activo. Especificá un proyecto o activá uno con switch_project."
        project = await db.fetchone("SELECT * FROM projects WHERE id = ?", (active_id,))
        if not project:
            return "Proyecto activo no encontrado en la base de datos."

    await db.update_project_objective(project["id"], objective)
    if objective:
        return f"Objective del proyecto '{project['name']}' actualizado: {objective}"
    return f"Objective del proyecto '{project['name']}' eliminado."


@registry.tool(
    name="get_project_objective",
    description=(
        "Obtiene el objective del proyecto activo o de uno especificado."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name_or_slug": {
                "type": "string",
                "description": "Nombre o slug del proyecto (opcional, usa el activo si se omite).",
            },
        },
        "required": [],
    },
)
async def get_project_objective(name_or_slug: str = "") -> str:
    from bbclaw.memory.db import get_db
    db = get_db()

    if name_or_slug:
        project, candidates = await _find_project(name_or_slug)
        if project is None:
            if candidates:
                slugs = ", ".join(p["slug"] for p in candidates)
                return f"Múltiples proyectos coinciden: {slugs}. Sé más específico."
            return f"Proyecto no encontrado: '{name_or_slug}'."
    else:
        active_id = getattr(get_current_session(), "active_project_id", None)
        if not active_id:
            return "No hay proyecto activo. Especificá un proyecto o activá uno con switch_project."
        project = await db.fetchone("SELECT * FROM projects WHERE id = ?", (active_id,))
        if not project:
            return "Proyecto activo no encontrado en la base de datos."

    obj = project.get("objective") or ""
    if obj:
        return f"Proyecto '{project['name']}' — Objective: {obj}"
    return f"Proyecto '{project['name']}' no tiene objective definido."
