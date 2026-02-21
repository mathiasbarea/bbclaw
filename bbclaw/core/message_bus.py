"""
Message Bus — sistema de eventos async inter-agente.
Permite que los agentes se comuniquen sin acoplamiento directo.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


@dataclass
class Event:
    type: str       # e.g. "task.completed", "task.failed", "agent.log"
    source: str     # nombre del agente que emitió el evento
    payload: Any = None


Handler = Callable[[Event], Awaitable[None]]


class MessageBus:
    """
    Bus de mensajes async basado en asyncio.Queue.
    Los agentes se suscriben a tipos de eventos y reciben notificaciones.
    """

    def __init__(self):
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._running = False
        self._task: asyncio.Task | None = None

    def subscribe(self, event_type: str, handler: Handler) -> None:
        """Suscribirse a un tipo de evento."""
        self._subscribers[event_type].append(handler)
        logger.debug("Subscripción: %s → %s", event_type, handler.__name__)

    def subscribe_all(self, handler: Handler) -> None:
        """Suscribirse a TODOS los eventos."""
        self._subscribers["*"].append(handler)

    async def publish(self, event: Event) -> None:
        """Publicar un evento en el bus."""
        await self._queue.put(event)

    async def publish_sync(self, event: Event) -> None:
        """Publicar y despachar inmediatamente (sin cola)."""
        await self._dispatch(event)

    async def _dispatch(self, event: Event) -> None:
        handlers = self._subscribers.get(event.type, []) + self._subscribers.get("*", [])
        if handlers:
            await asyncio.gather(*[h(event) for h in handlers], return_exceptions=True)

    async def start(self) -> None:
        """Inicia el loop de despacho de eventos."""
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.debug("MessageBus iniciado")

    async def _loop(self) -> None:
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=0.5)
                await self._dispatch(event)
                self._queue.task_done()
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error("Error en MessageBus loop: %s", e)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


# Instancia global compartida
bus = MessageBus()
