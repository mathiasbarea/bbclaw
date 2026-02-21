"""
Sistema de Skills/Plugins — carga dinámica de herramientas desde el directorio skills/.

Cada skill es un archivo Python en skills/ que define herramientas
usando el registro global. Se cargan al iniciar el sistema y
pueden agregarse/recargarse en runtime.

Estructura de un skill:
    skills/
    └── web_search.py     ← el skill
        └── SKILL_META = {"name": "...", "description": "..."}
        └── async def my_tool(...): ...
        └── registry.register("my_tool", ...)
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

from ..tools.registry import registry

logger = logging.getLogger(__name__)

SKILLS_DIR = Path("skills")
_loaded_skills: dict[str, Any] = {}


def set_skills_dir(path: str | Path) -> None:
    global SKILLS_DIR
    SKILLS_DIR = Path(path)
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)


def load_all_skills() -> list[str]:
    """
    Carga todos los skills desde el directorio skills/.
    Retorna lista de nombres de skills cargados.
    """
    if not SKILLS_DIR.exists():
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        _create_example_skill()
        return []

    loaded = []
    for skill_file in sorted(SKILLS_DIR.glob("*.py")):
        if skill_file.name.startswith("_"):
            continue
        result = load_skill(skill_file)
        if result:
            loaded.append(skill_file.stem)

    logger.info("Skills cargados: %s", loaded or ["ninguno"])
    return loaded


def load_skill(skill_path: Path) -> bool:
    """
    Carga un skill individual desde un archivo .py.
    El skill se auto-registra en el ToolRegistry al importarse.
    Retorna True si se cargó exitosamente.
    """
    skill_name = skill_path.stem
    try:
        spec = importlib.util.spec_from_file_location(
            f"bbclaw_skill_{skill_name}", skill_path
        )
        if spec is None or spec.loader is None:
            logger.warning("No se pudo crear spec para: %s", skill_path)
            return False

        module = importlib.util.module_from_spec(spec)
        sys.modules[f"bbclaw_skill_{skill_name}"] = module
        spec.loader.exec_module(module)

        meta = getattr(module, "SKILL_META", {"name": skill_name})
        _loaded_skills[skill_name] = {
            "module": module,
            "meta": meta,
            "path": str(skill_path),
        }
        logger.info("Skill cargado: %s", skill_name)
        return True

    except Exception as e:
        logger.error("Error al cargar skill '%s': %s", skill_name, e)
        return False


def reload_skill(skill_name: str) -> bool:
    """Recarga un skill ya cargado (útil para auto-mejora en caliente)."""
    info = _loaded_skills.get(skill_name)
    if not info:
        logger.warning("Skill '%s' no encontrado para reload", skill_name)
        return False
    return load_skill(Path(info["path"]))


def list_loaded_skills() -> list[dict]:
    """Lista todos los skills actualmente cargados."""
    return [
        {"name": name, "meta": info["meta"], "path": info["path"]}
        for name, info in _loaded_skills.items()
    ]


def _create_example_skill() -> None:
    """Crea un skill de ejemplo en skills/ si está vacío."""
    example = SKILLS_DIR / "example_skill.py"
    if example.exists():
        return

    example.write_text(
        '"""\nSkill de ejemplo — plantilla para crear nuevas herramientas.\n'
        'Copiá este archivo y modificalo para agregar tus propias herramientas.\n"""\n\n'
        'from bbclaw.tools.registry import registry\n\n'
        'SKILL_META = {\n    "name": "example",\n    "version": "0.1",\n'
        '    "description": "Skill de ejemplo"\n}\n\n\n'
        'async def _hello(name: str = "mundo") -> str:\n'
        '    return f"¡Hola, {name}! (desde skill de ejemplo)"\n\n\n'
        'registry.register(\n    name="hello",\n'
        '    description="Dice hola. Skill de ejemplo.",\n'
        '    func=_hello,\n'
        '    parameters={\n        "type": "object",\n'
        '        "properties": {"name": {"type": "string", "default": "mundo"}},\n'
        '        "required": [],\n    },\n)\n',
        encoding="utf-8",
    )
    logger.info("Skill de ejemplo creado en: %s", example)
