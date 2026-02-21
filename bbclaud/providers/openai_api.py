"""
Proveedor OpenAI con API Key estándar.
Alternativa para cuando se tiene una API key convencional.
"""

from __future__ import annotations

import json
import logging
import os

import httpx

from .base import LLMProvider, LLMResponse, Message, ToolCall

logger = logging.getLogger(__name__)


class OpenAIAPIProvider(LLMProvider):
    """Proveedor LLM usando API Key de OpenAI (autenticación estándar)."""

    _DEFAULT_MODEL = "gpt-4o"
    _BASE_URL = "https://api.openai.com/v1"

    def __init__(self, model: str | None = None, api_key: str | None = None, base_url: str | None = None):
        self._model = model or self._DEFAULT_MODEL
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._base_url = base_url or self._BASE_URL
        if not self._api_key:
            raise ValueError("OPENAI_API_KEY no está configurado.")
        self._client = httpx.AsyncClient(timeout=120)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _messages_to_dict(self, messages: list[Message]) -> list[dict]:
        result = []
        for m in messages:
            d: dict = {"role": m.role, "content": m.content}
            if m.tool_call_id:
                d["tool_call_id"] = m.tool_call_id
            if m.name:
                d["name"] = m.name
            result.append(d)
        return result

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        body: dict = {
            "model": self._model,
            "messages": self._messages_to_dict(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        resp = await self._client.post(
            f"{self._base_url}/chat/completions",
            headers=self._headers(),
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        msg = choice["message"]
        content = msg.get("content")
        finish_reason = choice.get("finish_reason", "stop")

        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            tool_calls.append(
                ToolCall(
                    id=tc["id"],
                    name=tc["function"]["name"],
                    arguments=json.loads(tc["function"]["arguments"]),
                )
            )

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=data.get("usage", {}),
        )

    async def embed(self, text: str) -> list[float]:
        resp = await self._client.post(
            f"{self._base_url}/embeddings",
            headers=self._headers(),
            json={"model": "text-embedding-3-small", "input": text},
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]

    @property
    def model(self) -> str:
        return self._model

    @property
    def supports_tools(self) -> bool:
        return True

    async def aclose(self) -> None:
        await self._client.aclose()
