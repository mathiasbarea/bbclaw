"""
Interfaz abstracta para proveedores LLM.
Todos los proveedores deben implementar esta interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str | list[dict]  # str o lista de content parts
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"  # "stop" | "tool_calls" | "length"
    usage: dict[str, int] = field(default_factory=dict)


class LLMProvider(ABC):
    """Interface base para todos los proveedores de LLM."""

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Realiza una llamada de completado al LLM."""
        ...

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Genera un embedding para el texto dado."""
        ...

    @property
    @abstractmethod
    def model(self) -> str:
        """Nombre del modelo activo."""
        ...

    @property
    @abstractmethod
    def supports_tools(self) -> bool:
        """Indica si el proveedor soporta tool calling."""
        ...
