"""
Herramientas de auto-mejora — permiten al SelfImproverAgent leer y escribir
el código fuente del propio sistema bbclaud (fuera del workspace normal).
"""

from __future__ import annotations

import logging
import asyncio
import os
import subprocess
from pathlib import Path

import aiofiles
import aiofiles.os

from ..tools.registry import registry
from ..identity import SYSTEM_NAME

logger = logging.getLogger(__name__)

# Path raíz del proyecto (se detecta automáticamente)
_PROJECT_ROOT: Path | None = None


def get_project_root() -> Path:
    """Detecta el root del proyecto buscando pyproject.toml."""
    global _PROJECT_ROOT
    if _PROJECT_ROOT is None:
        candidate = Path.cwd()
        for _ in range(6):
            if (candidate / "pyproject.toml").exists():
                _PROJECT_ROOT = candidate
                break
            candidate = candidate.parent
        else:
            _PROJECT_ROOT = Path.cwd()
    return _PROJECT_ROOT


def _safe_source_path(relative_path: str) -> Path:
    """
    Convierte un path relativo al proyecto a absoluto.
    Solo permite acceso dentro del proyecto (no fuera de él).
    """
    base = get_project_root().resolve()
    target = (base / relative_path).resolve()
    if not str(target).startswith(str(base)):
        raise ValueError(f"Acceso denegado: '{relative_path}' está fuera del proyecto")
    return target


async def _read_source(path: str) -> str:
    """Lee un archivo del código fuente del sistema."""
    full = _safe_source_path(path)
    if not full.exists():
        raise FileNotFoundError(f"No existe: {path}")
    async with aiofiles.open(full, "r", encoding="utf-8") as f:
        return await f.read()


async def _write_source(path: str, content: str) -> str:
    """Escribe un archivo del código fuente del sistema."""
    full = _safe_source_path(path)
    full.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(full, "w", encoding="utf-8") as f:
        await f.write(content)
    return f"✓ Archivo fuente escrito: {path} ({len(content)} chars)"


async def _list_source(directory: str = ".") -> str:
    """Lista archivos del código fuente del sistema."""
    full = _safe_source_path(directory)
    if not full.exists():
        raise FileNotFoundError(f"Directorio no encontrado: {directory}")

    items = []
    for item in sorted(full.rglob("*")):
        if any(part.startswith(".") or part == "__pycache__" for part in item.parts):
            continue
        rel = item.relative_to(get_project_root())
        kind = "📁" if item.is_dir() else "📄"
        size = f" ({item.stat().st_size}b)" if item.is_file() else ""
        items.append(f"{kind} {rel}{size}")

    return "\n".join(items[:80]) if items else "(vacío)"


async def _run_tests(test_path: str = "tests/") -> str:
    """Ejecuta la suite de tests del sistema."""
    proc = await asyncio.create_subprocess_shell(
        f"python -m pytest {test_path} -v --tb=short",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(get_project_root()),
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
    output = stdout.decode("utf-8", errors="replace")
    return f"[exit: {proc.returncode}]\n{output}"


async def _git_commit(message: str) -> str:
    """Hace git add -A y git commit con el mensaje dado."""
    cwd = str(get_project_root())

    async def _run(cmd: str) -> str:
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, cwd=cwd
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        return out.decode("utf-8", errors="replace")

    add_out = await _run("git add -A")
    commit_out = await _run(f"git commit -m '{message}'")
    return f"git add:\n{add_out}\ngit commit:\n{commit_out}"


# ── Registro ──────────────────────────────────────────────────────────────────

registry.register(
    name="read_source",
    description=f"Lee un archivo local del codigo fuente del sistema {SYSTEM_NAME} (para auto-mejora). Path relativo al root del proyecto.",
    func=_read_source,
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path relativo al proyecto, ej: bbclaud/core/agent.py"}},
        "required": ["path"],
    },
)

registry.register(
    name="write_source",
    description=f"Escribe/modifica un archivo del código fuente del sistema {SYSTEM_NAME}. SIEMPRE corroborá con run_tests después de usar esto.",
    func=_write_source,
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relativo, ej: bbclaud/core/agent.py"},
            "content": {"type": "string", "description": "Contenido completo a escribir"},
        },
        "required": ["path", "content"],
    },
)

registry.register(
    name="list_source",
    description=f"Lista archivos del código fuente del sistema {SYSTEM_NAME}.",
    func=_list_source,
    parameters={
        "type": "object",
        "properties": {"directory": {"type": "string", "description": "Directorio relativo al proyecto (default: raíz)", "default": "."}},
        "required": [],
    },
)

registry.register(
    name="run_tests",
    description=f"Ejecuta los tests del sistema {SYSTEM_NAME}. Úsalo siempre después de modificar el código fuente.",
    func=_run_tests,
    parameters={
        "type": "object",
        "properties": {"test_path": {"type": "string", "description": "Path de tests a ejecutar (default: tests/)", "default": "tests/"}},
        "required": [],
    },
)

registry.register(
    name="git_commit",
    description="Hace git add -A y git commit. Úsalo solo después de verificar que los tests pasan.",
    func=_git_commit,
    parameters={
        "type": "object",
        "properties": {"message": {"type": "string", "description": "Mensaje del commit"}},
        "required": ["message"],
    },
)

