"""
Herramientas de filesystem para los agentes.
Operan siempre dentro del workspace configurado.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import aiofiles
import aiofiles.os

from .registry import registry

logger = logging.getLogger(__name__)

# Workspace root — se setea al inicializar el sistema
_WORKSPACE_ROOT: Path = Path("workspace")


def set_workspace(path: str | Path) -> None:
    global _WORKSPACE_ROOT
    _WORKSPACE_ROOT = Path(path).resolve()
    _WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)


def get_workspace_root() -> Path:
    """Retorna el workspace root actual. Siempre refleja el valor más reciente."""
    return _WORKSPACE_ROOT


def _safe_path(relative_path: str) -> Path:
    """
    Convierte un path relativo a absoluto dentro del workspace.
    Lanza ValueError si el path intenta salir del workspace.
    """
    base = _WORKSPACE_ROOT.resolve()
    target = (base / relative_path).resolve()
    if not str(target).startswith(str(base)):
        raise ValueError(f"Acceso denegado: '{relative_path}' está fuera del workspace")
    return target


# ── Funciones de herramientas ─────────────────────────────────────────────────


async def _read_file(path: str) -> str:
    full = _safe_path(path)
    if not full.exists():
        raise FileNotFoundError(f"Archivo no encontrado: {path}")
    async with aiofiles.open(full, "r", encoding="utf-8") as f:
        return await f.read()


async def _write_file(path: str, content: str) -> str:
    full = _safe_path(path)
    full.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(full, "w", encoding="utf-8") as f:
        await f.write(content)
    return f"Archivo escrito: {path} ({len(content)} caracteres)"


async def _append_file(path: str, content: str) -> str:
    full = _safe_path(path)
    full.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(full, "a", encoding="utf-8") as f:
        await f.write(content)
    return f"Contenido agregado a: {path}"


async def _delete_file(path: str) -> str:
    full = _safe_path(path)
    if not full.exists():
        raise FileNotFoundError(f"Archivo no encontrado: {path}")
    await aiofiles.os.remove(full)
    return f"Archivo eliminado: {path}"


async def _list_files(directory: str = ".") -> str:
    full = _safe_path(directory)
    if not full.exists():
        raise FileNotFoundError(f"Directorio no encontrado: {directory}")
    items = []
    for item in sorted(full.iterdir()):
        rel = item.relative_to(_WORKSPACE_ROOT)
        kind = "📁" if item.is_dir() else "📄"
        size = f" ({item.stat().st_size} bytes)" if item.is_file() else ""
        items.append(f"{kind} {rel}{size}")
    return "\n".join(items) if items else "(directorio vacío)"


async def _make_dir(path: str) -> str:
    full = _safe_path(path)
    full.mkdir(parents=True, exist_ok=True)
    return f"Directorio creado: {path}"


# ── Registro en el registry global ───────────────────────────────────────────

registry.register(
    name="read_file",
    description="Lee el contenido de un archivo dentro del workspace del agente.",
    func=_read_file,
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Ruta relativa al workspace del archivo a leer"},
        },
        "required": ["path"],
    },
)

registry.register(
    name="write_file",
    description="Escribe (o sobreescribe) contenido en un archivo dentro del workspace. Crea directorios si es necesario.",
    func=_write_file,
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Ruta relativa al workspace"},
            "content": {"type": "string", "description": "Contenido a escribir"},
        },
        "required": ["path", "content"],
    },
)

registry.register(
    name="append_file",
    description="Agrega contenido al final de un archivo existente (o lo crea si no existe).",
    func=_append_file,
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Ruta relativa al workspace"},
            "content": {"type": "string", "description": "Contenido a agregar"},
        },
        "required": ["path", "content"],
    },
)

registry.register(
    name="delete_file",
    description="Elimina un archivo del workspace.",
    func=_delete_file,
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Ruta relativa al workspace del archivo a eliminar"},
        },
        "required": ["path"],
    },
)

registry.register(
    name="list_files",
    description="Lista archivos y directorios dentro del workspace.",
    func=_list_files,
    parameters={
        "type": "object",
        "properties": {
            "directory": {
                "type": "string",
                "description": "Directorio a listar (relativo al workspace). Por defecto: raíz del workspace.",
                "default": ".",
            },
        },
        "required": [],
    },
)

registry.register(
    name="make_dir",
    description="Crea un directorio (y subdirectorios) dentro del workspace.",
    func=_make_dir,
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Ruta relativa al workspace"},
        },
        "required": ["path"],
    },
)


async def _edit_file(path: str, old_string: str, new_string: str) -> str:
    """Edición quirúrgica: reemplaza old_string por new_string en el archivo."""
    full = _safe_path(path)
    if not full.exists():
        raise FileNotFoundError(f"Archivo no encontrado: {path}")

    async with aiofiles.open(full, "r", encoding="utf-8") as f:
        content = await f.read()

    count = content.count(old_string)
    if count == 0:
        raise ValueError(
            f"old_string not found in {path} — read the file first to get the exact text"
        )
    if count > 1:
        raise ValueError(
            f"old_string found {count} times in {path} — provide a longer snippet to match exactly once"
        )

    new_content = content.replace(old_string, new_string, 1)
    async with aiofiles.open(full, "w", encoding="utf-8") as f:
        await f.write(new_content)

    # Build preview: show 3 lines of context around the change
    new_lines = new_content.splitlines()
    # Find the first line of new_string in the result
    first_new_line = new_string.splitlines()[0] if new_string else ""
    center = 0
    for i, line in enumerate(new_lines):
        if first_new_line and first_new_line in line:
            center = i
            break

    start = max(0, center - 3)
    end = min(len(new_lines), center + 4)
    preview = "\n".join(
        f"  {i + 1:4d} | {new_lines[i]}" for i in range(start, end)
    )
    return f"Edited {path} successfully.\nPreview:\n{preview}"


_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox", ".mypy_cache"}
_BINARY_EXTS = {".pyc", ".pyo", ".exe", ".dll", ".so", ".dylib", ".zip", ".tar", ".gz", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".db", ".sqlite"}


async def _search_files(pattern: str, directory: str = ".", max_results: int = 20) -> str:
    """Grep recursivo dentro del workspace."""
    base = _safe_path(directory)
    if not base.exists():
        raise FileNotFoundError(f"Directorio no encontrado: {directory}")

    compiled = re.compile(pattern)
    results: list[str] = []

    for item in base.rglob("*"):
        if len(results) >= max_results:
            break
        # Skip directories and binary files
        if item.is_dir():
            continue
        if any(p in _SKIP_DIRS for p in item.parts):
            continue
        if item.suffix.lower() in _BINARY_EXTS:
            continue

        try:
            async with aiofiles.open(item, "r", encoding="utf-8", errors="ignore") as f:
                lines = await f.readlines()
            for lineno, line in enumerate(lines, 1):
                if compiled.search(line):
                    rel = item.relative_to(_WORKSPACE_ROOT)
                    results.append(f"{rel}:{lineno}: {line.rstrip()}")
                    if len(results) >= max_results:
                        break
        except (OSError, UnicodeDecodeError):
            continue

    if not results:
        return f"No matches found for pattern: {pattern}"
    header = f"Found {len(results)} match(es):\n"
    return header + "\n".join(results)


registry.register(
    name="edit_file",
    description=(
        "Edición quirúrgica: reemplaza old_string por new_string en un archivo. "
        "old_string debe aparecer exactamente 1 vez. Si no se encuentra o aparece "
        "múltiples veces, lanza error. Usar read_file primero para obtener el texto exacto."
    ),
    func=_edit_file,
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Ruta relativa al workspace del archivo a editar"},
            "old_string": {"type": "string", "description": "Texto exacto a buscar y reemplazar (debe aparecer 1 sola vez)"},
            "new_string": {"type": "string", "description": "Texto de reemplazo"},
        },
        "required": ["path", "old_string", "new_string"],
    },
)

registry.register(
    name="search_files",
    description=(
        "Busca un patrón regex en todos los archivos del workspace (grep recursivo). "
        "Retorna path:lineno:contenido. Útil para encontrar definiciones, usos, o texto."
    ),
    func=_search_files,
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Patrón regex a buscar"},
            "directory": {
                "type": "string",
                "description": "Subdirectorio donde buscar (relativo al workspace). Default: raíz.",
                "default": ".",
            },
            "max_results": {
                "type": "integer",
                "description": "Máximo de resultados a retornar. Default: 20.",
                "default": 20,
            },
        },
        "required": ["pattern"],
    },
)


async def _check_path(path: str) -> str:
    """
    Verifica si un path existe en el sistema de archivos.
    Acepta paths absolutos (para verificar fuera del workspace)
    o relativos (resueltos desde el workspace actual).
    """
    p = Path(path)
    if not p.is_absolute():
        p = (_WORKSPACE_ROOT / path).resolve()

    if not p.exists():
        return f"No existe: {p}"

    if p.is_file():
        size = p.stat().st_size
        return f"Archivo: {p}\n  Tamaño: {size} bytes"

    if p.is_dir():
        try:
            items = list(p.iterdir())
            n_files = sum(1 for i in items if i.is_file())
            n_dirs = sum(1 for i in items if i.is_dir())
            sample = [i.name for i in sorted(items)[:8]]
            sample_str = ", ".join(sample) + ("..." if len(items) > 8 else "")
            return (
                f"Directorio: {p}\n"
                f"  {n_files} archivos, {n_dirs} subdirectorios\n"
                f"  Contenido: {sample_str}"
            )
        except PermissionError:
            return f"Directorio (sin permiso de lectura): {p}"

    return f"Existe (tipo especial): {p}"


registry.register(
    name="check_path",
    description=(
        "Verifica si un path existe en el sistema de archivos y retorna su info. "
        "Acepta paths ABSOLUTOS (para verificar rutas fuera del workspace) "
        "o relativos al workspace actual. "
        "Úsalo para confirmar que una carpeta o archivo fue creado correctamente."
    ),
    func=_check_path,
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Path a verificar. Puede ser absoluto "
                    "(ej: C:\\\\Users\\\\mathi\\\\Documents\\\\mi-proyecto) "
                    "o relativo al workspace actual."
                ),
            },
        },
        "required": ["path"],
    },
)
