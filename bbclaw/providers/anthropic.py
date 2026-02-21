"""
Proveedor Anthropic Claude con API Key.
Soporta Claude claude-opus-4-5 y modelos anteriores.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from .base import LLMProvider, LLMResponse, Message, ToolCall

logger = logging.getLogger(__name__)


class AnthropicProvider(LLMProvider):
    """
    Proveedor LLM usando la API de Anthropic (Claude).
    Compatible con la interface LLMProvider.
    """

    _DEFAULT_MODEL = "claude-opus-4-5"
    _BASE_URL = "https://api.anthropic.com/v1"
    _API_VERSION = "2023-06-01"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        self._model = model or self._DEFAULT_MODEL
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._base_url = base_url or self._BASE_URL
        if not self._api_key:
            raise ValueError("ANTHROPIC_API_KEY no está configurado.")
        self._client = httpx.AsyncClient(timeout=120)

    def _headers(self) -> dict:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": self._API_VERSION,
            "content-type": "application/json",
        }

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        """Convierte schemas OpenAI-style al formato Anthropic."""
        anthropic_tools = []
        for t in tools:
            fn = t.get("function", {})
            anthropic_tools.append({
                "name": fn.get("name"),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {}),
            })
        return anthropic_tools

    def _convert_messages(self, messages: list[Message]) -> tuple[str, list[dict]]:
        """
        Separa system prompt de los mensajes y convierte al formato Anthropic.
        Retorna (system_text, messages_list).
        """
        system_parts = []
        converted = []

        for m in messages:
            if isinstance(m, dict):
                role = m.get("role", "")
                content = m.get("content", "")
                tool_calls = m.get("tool_calls")
            else:
                role = m.role
                content = m.content
                tool_calls = m.__dict__.get("_raw_tool_calls")

            if role == "system":
                system_parts.append(str(content or ""))
                continue

            if role == "assistant":
                if tool_calls:
                    # Mensaje con tool_use
                    blocks = []
                    if content:
                        blocks.append({"type": "text", "text": str(content)})
                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        args = fn.get("arguments", "{}")
                        blocks.append({
                            "type": "tool_use",
                            "id": tc["id"],
                            "name": fn.get("name"),
                            "input": json.loads(args) if isinstance(args, str) else args,
                        })
                    converted.append({"role": "assistant", "content": blocks})
                else:
                    converted.append({"role": "assistant", "content": str(content or "")})

            elif role == "tool":
                # Resultado de tool → tool_result block en el mensaje "user" siguiente
                if converted and converted[-1]["role"] == "user" and isinstance(converted[-1]["content"], list):
                    converted[-1]["content"].append({
                        "type": "tool_result",
                        "tool_use_id": m.tool_call_id if isinstance(m, Message) else m.get("tool_call_id"),
                        "content": str(content or ""),
                    })
                else:
                    converted.append({
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": m.tool_call_id if isinstance(m, Message) else m.get("tool_call_id"),
                            "content": str(content or ""),
                        }],
                    })

            elif role == "user":
                converted.append({"role": "user", "content": str(content or "")})

        return "\n\n".join(system_parts), converted

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        system_text, converted_messages = self._convert_messages(messages)

        body: dict[str, Any] = {
            "model": self._model,
            "messages": converted_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_text:
            body["system"] = system_text
        if tools:
            body["tools"] = self._convert_tools(tools)

        resp = await self._client.post(
            f"{self._base_url}/messages",
            headers=self._headers(),
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

        content_text: str | None = None
        tool_calls: list[ToolCall] = []
        finish_reason = data.get("stop_reason", "end_turn")

        for block in data.get("content", []):
            if block["type"] == "text":
                content_text = block["text"]
            elif block["type"] == "tool_use":
                tool_calls.append(ToolCall(
                    id=block["id"],
                    name=block["name"],
                    arguments=block.get("input", {}),
                ))

        # Mapear stop_reason al formato estándar
        if finish_reason == "tool_use":
            finish_reason = "tool_calls"
        elif finish_reason == "end_turn":
            finish_reason = "stop"

        return LLMResponse(
            content=content_text,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=data.get("usage", {}),
        )

    async def embed(self, text: str) -> list[float]:
        """
        Anthropic no tiene API de embeddings nativa.
        Fallback: retorna vector vacío — el sistema usará embeddings locales.
        """
        raise NotImplementedError(
            "Anthropic no provee API de embeddings. "
            "Usá embedding_provider=local en la config."
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def supports_tools(self) -> bool:
        return True

    async def aclose(self) -> None:
        await self._client.aclose()
