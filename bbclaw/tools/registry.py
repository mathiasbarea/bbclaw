"""
Registro dinámico de herramientas.
Las herramientas se registran con un decorador @tool o manualmente.
Genera schemas JSON compatibles con la spec de OpenAI tool calling.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


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

    async def call(self, name: str, **kwargs) -> ToolResult:
        """Ejecuta una herramienta por nombre."""
        if name not in self._tools:
            return ToolResult(success=False, output=None, error=f"Herramienta '{name}' no encontrada")

        tool = self._tools[name]
        try:
            result = await tool.func(**kwargs)
            return ToolResult(success=True, output=result)
        except Exception as e:
            logger.error("Error al ejecutar tool '%s': %s", name, e)
            return ToolResult(success=False, output=None, error=str(e))

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

    def __contains__(self, name: str) -> bool:
        return name in self._tools


# Instancia global del registro
registry = ToolRegistry()
