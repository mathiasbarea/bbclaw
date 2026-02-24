"""
Herramientas de gestión de artifacts para agentes.
Permite guardar, consultar y listar artifacts vinculados al proyecto activo.
Append inteligente: si ya existe un artifact con el mismo título, agrega contenido.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from .registry import registry

logger = logging.getLogger(__name__)


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


@registry.tool(
    name="save_artifact",
    description=(
        "Guarda o actualiza un artifact del proyecto activo. "
        "Si ya existe uno con el mismo título, agrega el contenido nuevo (append inteligente). "
        "Usá esto para persistir resultados valiosos: ideas, reportes, análisis, listas, etc."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Título del artifact (ej: 'Ideas AB Test', 'Análisis SEO').",
            },
            "content": {
                "type": "string",
                "description": "Contenido a guardar (texto, markdown, etc).",
            },
            "artifact_type": {
                "type": "string",
                "description": "Tipo: general, ideas, report, analysis, checklist, code, data. Default: general.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tags opcionales para categorizar.",
            },
        },
        "required": ["title", "content"],
    },
)
async def save_artifact(
    title: str,
    content: str,
    artifact_type: str = "general",
    tags: list[str] | None = None,
) -> str:
    from bbclaw.memory.db import get_db
    from .projects import get_current_session

    db = get_db()
    session = get_current_session()
    project_id = getattr(session, "active_project_id", None) or ""

    existing = await db.get_artifact_by_title(title, project_id)

    if existing:
        new_version = (existing.get("version") or 1) + 1
        new_content = (
            existing["content"]
            + f"\n\n### Run {new_version} — {_now_str()}\n{content}"
        )
        await db.update_artifact_content(existing["id"], new_content, new_version)
        return (
            f"Artifact '{title}' actualizado (v{new_version}). "
            f"ID: {existing['id']}"
        )

    artifact_id = str(uuid.uuid4())
    full_content = f"### Run 1 — {_now_str()}\n{content}"
    await db.create_artifact(
        artifact_id=artifact_id,
        project_id=project_id,
        title=title,
        artifact_type=artifact_type,
        content=full_content,
        tags=tags,
    )
    return f"Artifact '{title}' creado. ID: {artifact_id}"


@registry.tool(
    name="get_artifact",
    description=(
        "Obtiene el contenido completo de un artifact por título o ID."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title_or_id": {
                "type": "string",
                "description": "Título o ID del artifact a consultar.",
            },
        },
        "required": ["title_or_id"],
    },
)
async def get_artifact(title_or_id: str) -> str:
    from bbclaw.memory.db import get_db
    from .projects import get_current_session

    db = get_db()

    # Try by ID first
    artifact = await db.get_artifact(title_or_id)
    if not artifact:
        # Try by title in active project
        session = get_current_session()
        project_id = getattr(session, "active_project_id", None) or ""
        artifact = await db.get_artifact_by_title(title_or_id, project_id)

    if not artifact:
        return f"Artifact no encontrado: '{title_or_id}'"

    tags = json.loads(artifact.get("tags", "[]")) if isinstance(artifact.get("tags"), str) else artifact.get("tags", [])
    tags_str = ", ".join(tags) if tags else "sin tags"
    header = (
        f"# {artifact['title']}\n"
        f"Tipo: {artifact['artifact_type']} | v{artifact['version']} | Tags: {tags_str}\n"
        f"Actualizado: {artifact['updated_at']}\n\n"
    )
    return header + artifact["content"]


@registry.tool(
    name="list_artifacts",
    description="Lista los artifacts del proyecto activo.",
    parameters={"type": "object", "properties": {}, "required": []},
)
async def list_artifacts() -> str:
    from bbclaw.memory.db import get_db
    from .projects import get_current_session

    db = get_db()
    session = get_current_session()
    project_id = getattr(session, "active_project_id", None) or ""

    artifacts = await db.get_artifacts_by_project(project_id)
    if not artifacts:
        return "No hay artifacts en este proyecto."

    lines = ["Artifacts del proyecto:\n"]
    for a in artifacts:
        tags = json.loads(a.get("tags", "[]")) if isinstance(a.get("tags"), str) else a.get("tags", [])
        tags_str = f" [{', '.join(tags)}]" if tags else ""
        lines.append(
            f"  - **{a['title']}** ({a['artifact_type']}, v{a['version']}){tags_str}\n"
            f"    Actualizado: {a['updated_at'][:10]} | ID: {a['id']}"
        )
    return "\n".join(lines)
