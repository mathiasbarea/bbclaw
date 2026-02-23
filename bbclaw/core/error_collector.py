"""
ErrorCollector — logging handler que captura errores en memoria.
Permite al improvement loop detectar y reaccionar a errores del sistema.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ErrorRecord:
    id: str
    timestamp: float
    logger_name: str
    message: str
    traceback: str | None = None
    count: int = 1
    resolved: bool = False


_DEDUP_WINDOW_S = 60
_MAX_ERRORS = 50
_DEFAULT_MAX_AGE_MINUTES = 30


class ErrorCollector(logging.Handler):
    """
    Logging handler que captura ERROR/CRITICAL de loggers bbclaw.*
    en una cola circular con dedup.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self._errors: deque[ErrorRecord] = deque(maxlen=_MAX_ERRORS)
        self._dedup_keys: dict[str, ErrorRecord] = {}

    def emit(self, record: logging.LogRecord) -> None:
        # Solo capturar loggers de bbclaw
        if not record.name.startswith("bbclaw."):
            return
        # Anti-recursión: ignorar errores del propio improvement loop
        if record.name == "bbclaw.core.improvement_loop":
            return

        message = self.format(record) if self.formatter else record.getMessage()
        tb: str | None = None
        if record.exc_info and record.exc_info[1] is not None:
            import traceback
            tb = "".join(traceback.format_exception(*record.exc_info))

        dedup_key = f"{record.name}:{record.getMessage()}"
        now = time.time()

        # Dedup: si el mismo error ocurrió dentro de la ventana, incrementar counter
        existing = self._dedup_keys.get(dedup_key)
        if existing and not existing.resolved and (now - existing.timestamp) < _DEDUP_WINDOW_S:
            existing.count += 1
            existing.timestamp = now
            return

        rec = ErrorRecord(
            id=uuid.uuid4().hex[:8],
            timestamp=now,
            logger_name=record.name,
            message=message,
            traceback=tb,
        )
        self._errors.append(rec)
        self._dedup_keys[dedup_key] = rec

        # Limpiar claves de dedup viejas
        stale = [k for k, v in self._dedup_keys.items() if (now - v.timestamp) > _DEDUP_WINDOW_S * 2]
        for k in stale:
            del self._dedup_keys[k]

    def get_unresolved(self, max_age_minutes: float = _DEFAULT_MAX_AGE_MINUTES) -> list[ErrorRecord]:
        cutoff = time.time() - (max_age_minutes * 60)
        return [e for e in self._errors if not e.resolved and e.timestamp >= cutoff]

    def has_actionable_errors(self) -> bool:
        return len(self.get_unresolved()) > 0

    def mark_all_resolved(self) -> None:
        for e in self._errors:
            if not e.resolved:
                e.resolved = True

    def format_for_prompt(self) -> str:
        errors = self.get_unresolved()
        if not errors:
            return ""

        now = time.time()
        lines: list[str] = [f"=== ERRORES ACTIVOS ({len(errors)}) ===\n"]
        for e in errors:
            age_s = now - e.timestamp
            if age_s < 60:
                age_str = f"hace {int(age_s)}s"
            else:
                age_str = f"hace {int(age_s / 60)}min"

            header = f"[{e.id}] {e.logger_name} ({age_str})"
            if e.count > 1:
                header += f" x{e.count}"
            lines.append(header)
            lines.append(e.message)
            if e.traceback:
                lines.append(f"Traceback:\n{e.traceback}")
            lines.append("")

        return "\n".join(lines)
