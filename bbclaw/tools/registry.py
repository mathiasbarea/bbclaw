"""
Registro dinámico de herramientas.
Las herramientas se registran con un decorador @tool o manualmente.
Genera schemas JSON compatibles con la spec de OpenAI tool calling.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

_MUTATING_TOOLS: set[str] = {
    "write_file", "write_source", "append_file", "delete_file", "make_dir",
}
_auto_commit_enabled: bool = False


def enable_auto_commit() -> None:
    global _auto_commit_enabled
    _auto_commit_enabled = True


_TOOL_VERBS: dict[str, str] = {
    "write_file": "update",
    "write_source": "update",
    "append_file": "append to",
    "delete_file": "delete",
    "make_dir": "create dir",
}

_READ_TOOLS_WITH_PATH = {"read_file", "read_source"}


async def _auto_commit(tool_name: str, kwargs: dict) -> None:
    path_arg = str(kwargs.get("path", kwargs.get("directory", "")))
    verb = _TOOL_VERBS.get(tool_name, tool_name)
    # Escapar comillas dobles en el path para seguridad del shell
    safe_path = path_arg.replace('"', '\\"')
    msg = f"auto: {verb} {safe_path}"
    try:
        # Primero stage, luego ver si hay cambios reales con --cached,
        # solo commitear si hay diff staged
        proc = await asyncio.create_subprocess_shell(
            f'git add -A && git diff --cached --quiet || git commit -m "{msg}"',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.wait(), timeout=10)
    except Exception:
        pass  # silencioso — no bloquear al agente


def _normalize_tool_path(path_arg: Any) -> str:
    raw = "" if path_arg is None else str(path_arg)
    stripped = raw.strip()
    if stripped in {"", ".", "./", ".\\"}:
        return "."
    return str(Path(stripped))


def _build_actionable_path_error(tool_name: str, kwargs: dict, error: Exception) -> str:
    original = kwargs.get("path")
    normalized = _normalize_tool_path(original)
    msg = str(error)

    hints: list[str] = []
    if normalized == ".":
        hints.append("el path parece vacío o apunta al directorio actual")
    if any(seg in normalized for seg in ("..", "~")):
        hints.append("evitá usar '..' o '~'; pasá una ruta relativa al workspace")

    if "Archivo no encontrado" in msg or "No such file" in msg or "not found" in msg.lower():
        base = f"{msg}. Path recibido='{original}', normalizado='{normalized}'."
        suggestion = "Sugerencia: usá list_files/check_path antes de read_file/read_source para confirmar la ruta exacta."
        if hints:
            return f"{base} Posible causa: {'; '.join(hints)}. {suggestion}"
        return f"{base} {suggestion}"

    return msg


@dataclass
class ToolDefinition:
    name: str
    description: str
    func: Callable[..., Awaitable[Any]]
    parameters: dict  # JSON Schema de parámetros


@dataclass
class ToolResult:
    success: bool
    output: Any
    error: str | None = None

    def to_str(self) -> str:
        if self.success:
            return str(self.output)
        return f"ERROR: {self.error}"


class ToolRegistry:
    """Registro global de herramientas disponibles para los agentes."""

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(
        self,
        name: str,
        description: str,
        func: Callable,
        parameters: dict,
    ) -> None:
        self._tools[name] = ToolDefinition(
            name=name,
            description=description,
            func=func,
            parameters=parameters,
        )
        logger.debug("Herramienta registrada: %s", name)

    def tool(self, name: str, description: str, parameters: dict):
        """Decorador para registrar una función como herramienta."""

        def decorator(func):
            self.register(name, description, func, parameters)
            return func

        return decorator

    async def call(self, tool_name: str, **kwargs) -> ToolResult:
        """Ejecuta una herramienta por nombre."""
        if tool_name not in self._tools:
            return ToolResult(success=False, output=None, error=f"Herramienta '{tool_name}' no encontrada")

        if tool_name in _READ_TOOLS_WITH_PATH and "path" in kwargs:
            kwargs = {**kwargs, "path": _normalize_tool_path(kwargs.get("path"))}

        tool = self._tools[tool_name]
        try:
            result = await tool.func(**kwargs)
            tr = ToolResult(success=True, output=result)
        except Exception as e:
            logger.error("Error al ejecutar tool '%s': %s", tool_name, e)
            error_msg = _build_actionable_path_error(tool_name, kwargs, e) if tool_name in _READ_TOOLS_WITH_PATH else str(e)
            return ToolResult(success=False, output=None, error=error_msg)

        if tr.success and _auto_commit_enabled and tool_name in _MUTATING_TOOLS:
            await _auto_commit(tool_name, kwargs)

        return tr

    def get_schemas(self) -> list[dict]:
        """Devuelve los schemas JSON para la API de OpenAI tool calling."""
        schemas = []
        for tool in self._tools.values():
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
            )
        return schemas

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    def describe_for_prompt(self) -> str:
        """Genera un resumen de todas las herramientas registradas para inyectar en prompts."""
        if not self._tools:
            return "No hay herramientas disponibles."
        lines = []
        for tool in self._tools.values():
            params = tool.parameters.get("properties", {})
            param_names = ", ".join(params.keys()) if params else "ninguno"
            lines.append(f"- {tool.name}({param_names}): {tool.description}")
        return "\n".join(lines)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


# Instancia global del registro
registry = ToolRegistry()
