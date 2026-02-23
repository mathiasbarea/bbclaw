"""
Clase base Agent — implementa el loop de razonamiento + tool calling.
Cada agente especializado hereda de esta clase y define su system prompt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..providers.base import LLMProvider, Message, ToolCall
from ..tools.registry import ToolRegistry, ToolResult

logger = logging.getLogger(__name__)


@dataclass
class AgentContext:
    """Contexto de una ejecución de agente."""

    task_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    task_description: str = ""
    memory_context: str = ""  # contexto pre-construido por ContextBuilder
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    task_id: str
    agent_name: str
    success: bool
    output: str
    tool_calls_made: int = 0
    error: str | None = None


class Agent:
    """
    Agente base con loop de razonamiento + tool calling.

    El loop funciona así:
    1. Manda system prompt + historial de mensajes al LLM
    2. Si el LLM responde con tool_calls → ejecuta las herramientas → añade resultados → vuelve al paso 1
    3. Si el LLM responde con finish_reason="stop" → retorna la respuesta final
    """

    name: str = "Agent"
    description: str = ""

    def __init__(
        self,
        provider: LLMProvider,
        tool_registry: ToolRegistry,
        max_iterations: int = 20,
        temperature: float = 0.7,
    ):
        self.provider = provider
        self.tool_registry = tool_registry
        self.max_iterations = max_iterations
        self.temperature = temperature

    def system_prompt(self, context: AgentContext) -> str:
        """Override en subclases para customizar el prompt del sistema."""
        tools_desc = self.tool_registry.describe_for_prompt()
        base = f"""Eres {self.name}, {self.description}

Hoy tienes la siguiente tarea: {context.task_description}

Herramientas disponibles:
{tools_desc}

Reglas:
- Usa la herramienta más específica disponible para cada acción
- Sé preciso y conciso en tus respuestas finales
- Siempre verifica el resultado de los comandos antes de continuar"""

        if context.memory_context:
            base += f"\n\n--- Contexto relevante ---\n{context.memory_context}"

        return base

    async def run(self, context: AgentContext) -> AgentResult:
        """Ejecuta el agente con el contexto dado y devuelve el resultado."""
        messages: list[Message] = [
            Message(role="system", content=self.system_prompt(context)),
            Message(role="user", content=context.task_description),
        ]
        tools = self.tool_registry.get_schemas()
        tool_calls_count = 0

        for iteration in range(self.max_iterations):
            logger.debug("[%s] Iteración %d/%d", self.name, iteration + 1, self.max_iterations)

            # Build API-ready messages for the provider
            api_messages = self._build_api_messages(messages)

            response = await self._complete_with_retry(api_messages, tools)

            # Si el LLM quiere hacer tool calls → ejecutar y continuar
            if response.tool_calls:
                # Store assistant message with raw tool_calls for serialization
                tool_calls_dict = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in response.tool_calls
                ]
                assistant_msg = Message(role="assistant", content=None)  # type: ignore
                assistant_msg.__dict__["_raw_tool_calls"] = tool_calls_dict
                messages.append(assistant_msg)

                # Ejecutar cada tool call
                for tc in response.tool_calls:
                    tool_calls_count += 1
                    logger.info("[%s] Llamando tool: %s(%s)", self.name, tc.name, tc.arguments)
                    result: ToolResult = await self.tool_registry.call(tc.name, **tc.arguments)

                    messages.append(
                        Message(
                            role="tool",
                            content=result.to_str(),
                            tool_call_id=tc.id,
                            name=tc.name,
                        )
                    )

                continue  # Next iteration

            # Respuesta final del LLM
            final_content = response.content or ""
            logger.info("[%s] Completado en %d iteraciones", self.name, iteration + 1)
            return AgentResult(
                task_id=context.task_id,
                agent_name=self.name,
                success=True,
                output=final_content,
                tool_calls_made=tool_calls_count,
            )

        # Si agotamos iteraciones sin finish_reason=stop
        return AgentResult(
            task_id=context.task_id,
            agent_name=self.name,
            success=False,
            output="",
            tool_calls_made=tool_calls_count,
            error=f"Máximo de iteraciones ({self.max_iterations}) alcanzado sin respuesta final",
        )

    async def _complete_with_retry(
        self,
        api_messages: list[dict],
        tools: list[dict] | None,
        max_retries: int = 2,
    ):
        """
        Llama al provider con reintentos automáticos.
        - Errores transitorios (5xx, timeout, red): reintenta hasta max_retries veces.
        - Errores de cliente (4xx): falla inmediatamente con mensaje descriptivo.
        """
        import httpx

        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return await self.provider.complete(
                    messages=api_messages,  # type: ignore[arg-type]
                    tools=tools if tools else None,
                    temperature=self.temperature,
                )
            except httpx.HTTPStatusError as e:
                status = e.response.status_code if e.response is not None else 0
                body = ""
                try:
                    body = e.response.text if e.response is not None else ""
                except Exception:
                    pass

                if 400 <= status < 500:
                    # Error de cliente — no tiene sentido reintentar
                    raise RuntimeError(
                        f"Error del proveedor ({status}): {body[:300]}"
                    ) from e

                # Error de servidor — reintentar
                last_error = RuntimeError(f"Error del proveedor ({status}): {body[:200]}")
                logger.warning(
                    "[%s] Error transitorio (intento %d/%d): %s",
                    self.name, attempt + 1, max_retries + 1, last_error,
                )
            except (asyncio.TimeoutError, OSError, ConnectionError) as e:
                last_error = e
                logger.warning(
                    "[%s] Error de red (intento %d/%d): %s",
                    self.name, attempt + 1, max_retries + 1, e,
                )

            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)  # backoff: 1s, 2s

        raise last_error or RuntimeError("Error desconocido al llamar al proveedor")

    def _build_api_messages(self, messages: list[Message]) -> list[dict]:
        """Convierte mensajes internos al formato de la API de OpenAI."""
        result = []
        for m in messages:
            raw = m.__dict__.get("_raw_tool_calls")
            if raw is not None:
                # Mensaje assistant con tool_calls
                d = {"role": "assistant", "content": None, "tool_calls": raw}
                result.append(d)
            elif m.role == "tool":
                result.append({
                    "role": "tool",
                    "content": m.content,
                    "tool_call_id": m.tool_call_id,
                    "name": m.name,
                })
            else:
                result.append({"role": m.role, "content": m.content})
        return result
