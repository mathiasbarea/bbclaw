"""
Tests básicos de Fase 1.
Verifica que los módulos core cargan y funcionan sin proveedor real.
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bbclaw.providers.base import Message, LLMResponse, ToolCall
from bbclaw.tools.registry import ToolRegistry, ToolResult
from bbclaw.core.agent import Agent, AgentContext


# ── Tools ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_registry_basic():
    registry = ToolRegistry()

    async def sample_tool(x: int) -> str:
        return f"resultado: {x}"

    registry.register(
        name="sample",
        description="una herramienta de prueba",
        func=sample_tool,
        parameters={"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
    )

    assert "sample" in registry
    result = await registry.call("sample", x=42)
    assert result.success
    assert result.output == "resultado: 42"


@pytest.mark.asyncio
async def test_tool_registry_missing_tool():
    registry = ToolRegistry()
    result = await registry.call("nonexistent")
    assert not result.success
    assert "no encontrada" in result.error


@pytest.mark.asyncio
async def test_tool_registry_schemas():
    registry = ToolRegistry()

    async def noop() -> str:
        return "ok"

    registry.register("noop", "no hace nada", noop, {"type": "object", "properties": {}})
    schemas = registry.get_schemas()
    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "noop"


# ── Filesystem (con workspace temp) ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_filesystem_write_read(tmp_path):
    from bbclaw.tools.filesystem import set_workspace, _write_file, _read_file

    set_workspace(tmp_path)
    await _write_file("test.txt", "hola mundo")
    content = await _read_file("test.txt")
    assert content == "hola mundo"


@pytest.mark.asyncio
async def test_filesystem_sandbox(tmp_path):
    from bbclaw.tools.filesystem import set_workspace, _read_file

    set_workspace(tmp_path)
    with pytest.raises(ValueError, match="fuera del workspace"):
        await _read_file("../../etc/passwd")


# ── Memory DB ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_db_conversations(tmp_path):
    from bbclaw.memory.db import Database

    db = Database(tmp_path / "test.db")
    await db.connect()

    conv_id = await db.save_conversation("¿hola?", "¡Hola!")
    assert conv_id is not None

    history = await db.get_recent_conversations(10)
    assert len(history) == 1
    assert history[0]["user_msg"] == "¿hola?"

    await db.close()


@pytest.mark.asyncio
async def test_db_knowledge(tmp_path):
    from bbclaw.memory.db import Database

    db = Database(tmp_path / "test.db")
    await db.connect()

    await db.set_knowledge("clave", {"valor": 123})
    result = await db.get_knowledge("clave")
    assert result == {"valor": 123}

    await db.close()


# ── Agent (con provider mock) ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_simple_response():
    mock_provider = AsyncMock()
    mock_provider.complete = AsyncMock(return_value=LLMResponse(
        content="Respuesta de prueba",
        tool_calls=[],
        finish_reason="stop",
    ))

    registry = ToolRegistry()
    agent = Agent(provider=mock_provider, tool_registry=registry)
    agent.name = "TestAgent"
    agent.description = "agente de prueba"

    ctx = AgentContext(task_description="Dime hola")
    result = await agent.run(ctx)

    assert result.success
    assert result.output == "Respuesta de prueba"
    assert result.tool_calls_made == 0


@pytest.mark.asyncio
async def test_agent_with_tool_call():
    """Verifica que el agente ejecuta tools y luego retorna respuesta final."""
    mock_provider = AsyncMock()

    # Primera llamada: el LLM hace un tool call
    # Segunda llamada: retorna respuesta final
    mock_provider.complete = AsyncMock(side_effect=[
        LLMResponse(
            content=None,
            tool_calls=[ToolCall(id="tc1", name="sample_tool", arguments={"x": 1})],
            finish_reason="tool_calls",
        ),
        LLMResponse(
            content="Listo, usé la herramienta.",
            tool_calls=[],
            finish_reason="stop",
        ),
    ])

    registry = ToolRegistry()
    async def sample_tool(x: int) -> str:
        return f"resultado: {x}"
    registry.register("sample_tool", "herramienta de prueba", sample_tool,
                      {"type": "object", "properties": {"x": {"type": "integer"}}})

    agent = Agent(provider=mock_provider, tool_registry=registry)
    agent.name = "TestAgent"
    agent.description = "agente de prueba"

    ctx = AgentContext(task_description="Usá la herramienta con x=1")
    result = await agent.run(ctx)

    assert result.success
    assert result.output == "Listo, usé la herramienta."
    assert result.tool_calls_made == 1
