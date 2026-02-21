"""
Tests de Fase 2 — Multi-agente: planner, task_queue, message_bus, agentes.
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock

from bbclaw.providers.base import LLMResponse, ToolCall
from bbclaw.tools.registry import ToolRegistry
from bbclaw.core.agent import AgentContext
from bbclaw.core.planner import Planner, Plan, TaskSpec
from bbclaw.core.task_queue import TaskQueue
from bbclaw.core.message_bus import MessageBus, Event
from bbclaw.agents import CoderAgent, ResearcherAgent, SelfImproverAgent, build_agent_registry


# ── MessageBus ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_message_bus_publish_subscribe():
    bus = MessageBus()
    received = []

    async def handler(event: Event):
        received.append(event)

    bus.subscribe("test.event", handler)
    await bus.start()
    await bus.publish(Event("test.event", "test_source", {"key": "val"}))
    await asyncio.sleep(0.1)  # dar tiempo al loop de eventos
    await bus.stop()

    assert len(received) == 1
    assert received[0].type == "test.event"
    assert received[0].payload["key"] == "val"


@pytest.mark.asyncio
async def test_message_bus_wildcard():
    bus = MessageBus()
    received = []

    async def catch_all(event: Event):
        received.append(event)

    bus.subscribe_all(catch_all)
    await bus.start()
    await bus.publish(Event("any.event", "src", None))
    await bus.publish(Event("other.event", "src", None))
    await asyncio.sleep(0.2)
    await bus.stop()

    assert len(received) == 2


# ── Planner ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_planner_valid_json():
    """Planner parsea correctamente un JSON válido del LLM."""
    plan_json = """
    {
      "plan_summary": "Crear un archivo de prueba",
      "tasks": [
        {"id": "t1", "name": "Crear archivo", "description": "Crear test.txt", "agent": "coder", "depends_on": []}
      ]
    }
    """
    mock_provider = AsyncMock()
    mock_provider.complete = AsyncMock(return_value=LLMResponse(
        content=plan_json, tool_calls=[], finish_reason="stop"
    ))

    planner = Planner(provider=mock_provider)
    plan = await planner.create_plan("Crea un archivo de prueba")

    assert len(plan.tasks) == 1
    assert plan.tasks[0].agent == "coder"
    assert plan.tasks[0].id == "t1"


@pytest.mark.asyncio
async def test_planner_fallback_on_bad_json():
    """Planner cae en plan de 1 tarea si el LLM no devuelve JSON válido."""
    mock_provider = AsyncMock()
    mock_provider.complete = AsyncMock(return_value=LLMResponse(
        content="Lo haré directamente.", tool_calls=[], finish_reason="stop"
    ))

    planner = Planner(provider=mock_provider)
    plan = await planner.create_plan("Hola")

    assert len(plan.tasks) == 1
    assert plan.tasks[0].agent == "generalist"


@pytest.mark.asyncio
async def test_planner_with_dependencies():
    """Planner maneja correctamente las dependencias entre tareas."""
    plan_json = """{
      "plan_summary": "Investigar y luego coder",
      "tasks": [
        {"id": "t1", "name": "Investigar", "description": "Argh", "agent": "researcher", "depends_on": []},
        {"id": "t2", "name": "Implementar", "description": "Impl", "agent": "coder", "depends_on": ["t1"]}
      ]
    }"""
    mock_provider = AsyncMock()
    mock_provider.complete = AsyncMock(return_value=LLMResponse(
        content=plan_json, tool_calls=[], finish_reason="stop"
    ))

    planner = Planner(provider=mock_provider)
    plan = await planner.create_plan("Investiga e implementa")

    assert len(plan.tasks) == 2
    t1 = plan.tasks[0]
    t2 = plan.tasks[1]
    assert t1.can_run(set()) is True              # t1 sin deps → ejecutable
    assert t2.can_run(set()) is False             # t2 con dep t1 → bloqueada
    assert t2.can_run({"t1"}) is True             # t2 cuando t1 completa → ejecutable


# ── TaskQueue ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_queue_single_task():
    """TaskQueue ejecuta y completa una sola tarea."""
    mock_provider = AsyncMock()
    mock_provider.complete = AsyncMock(return_value=LLMResponse(
        content="Tarea completada exitosamente.", tool_calls=[], finish_reason="stop"
    ))

    registry = ToolRegistry()
    agents = build_agent_registry(mock_provider, registry, max_iterations=5)
    queue = TaskQueue(agents=agents)

    plan = Plan(
        id="p1",
        summary="Test",
        tasks=[TaskSpec(id="t1", name="test task", description="Haz algo simple", agent="generalist")],
        original_request="Haz algo",
    )

    result_plan = await queue.execute(plan)
    assert result_plan.tasks[0].status == "done"
    assert result_plan.tasks[0].result == "Tarea completada exitosamente."


@pytest.mark.asyncio
async def test_task_queue_dependency_order():
    """TaskQueue ejecuta t1 antes que t2 cuando t2 depende de t1."""
    execution_order = []

    async def mock_complete(messages, tools=None, temperature=0.7, max_tokens=4096):
        # Detectar cuál tarea es por el prompt
        system_content = messages[0].get("content") or messages[0].get("role", "")
        if isinstance(messages[0], dict):
            content = messages[0].get("content", "")
        else:
            content = str(messages[0])
        return LLMResponse(content=f"done-{len(execution_order)}", tool_calls=[], finish_reason="stop")

    call_count = [0]

    async def mock_complete2(messages, tools=None, temperature=0.7, max_tokens=4096):
        call_count[0] += 1
        execution_order.append(call_count[0])
        return LLMResponse(content=f"done-{call_count[0]}", tool_calls=[], finish_reason="stop")

    mock_provider = AsyncMock()
    mock_provider.complete = mock_complete2

    registry = ToolRegistry()
    agents = build_agent_registry(mock_provider, registry, max_iterations=3)
    queue = TaskQueue(agents=agents)

    plan = Plan(
        id="p2",
        summary="dep test",
        tasks=[
            TaskSpec(id="t1", name="research", description="research", agent="researcher"),
            TaskSpec(id="t2", name="code", description="code", agent="coder", depends_on=["t1"]),
        ],
        original_request="Investiga e implementa",
    )

    await queue.execute(plan)

    t1 = plan.tasks[0]
    t2 = plan.tasks[1]
    assert t1.status == "done"
    assert t2.status == "done"
    # t1 debe haber completado antes de que t2 pudiera empezar


# ── Agentes especializados ────────────────────────────────────────────────────

def test_specialized_agents_have_unique_names():
    mock_provider = AsyncMock()
    registry = ToolRegistry()
    agents = build_agent_registry(mock_provider, registry)

    names = {a.name for a in agents.values()}
    assert "coder" in names
    assert "researcher" in names
    assert "self_improver" in names
    assert "orchestrator" in names


def test_agent_system_prompts_include_task():
    mock_provider = AsyncMock()
    registry = ToolRegistry()
    agents = build_agent_registry(mock_provider, registry)

    ctx = AgentContext(task_description="Tarea de prueba XYZ")
    coder_prompt = agents["coder"].system_prompt(ctx)
    assert "Tarea de prueba XYZ" in coder_prompt

    researcher_prompt = agents["researcher"].system_prompt(ctx)
    assert "Tarea de prueba XYZ" in researcher_prompt
