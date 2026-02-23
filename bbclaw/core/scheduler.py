"""
Lógica pura de scheduling — sin I/O ni imports de bbclaw.
Solo datetime + math.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

_VALID_TYPES = {"once", "interval", "daily", "weekly", "monthly"}
_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def parse_schedule(spec: dict) -> dict:
    """Valida un schedule spec. Lanza ValueError si es inválido."""
    stype = spec.get("type")
    if stype not in _VALID_TYPES:
        raise ValueError(f"Tipo de schedule inválido: '{stype}'. Debe ser uno de {_VALID_TYPES}")

    if stype == "once":
        if "at" not in spec:
            raise ValueError("Schedule 'once' requiere campo 'at' (ISO8601)")
        parse_iso(spec["at"])  # validate

    elif stype == "interval":
        minutes = spec.get("minutes")
        if not isinstance(minutes, (int, float)) or minutes <= 0:
            raise ValueError("Schedule 'interval' requiere 'minutes' > 0")

    elif stype == "daily":
        _validate_time(spec)

    elif stype == "weekly":
        _validate_time(spec)
        day = spec.get("day", "").lower()
        if day not in _WEEKDAYS:
            raise ValueError(f"Día inválido: '{day}'. Debe ser uno de {list(_WEEKDAYS.keys())}")

    elif stype == "monthly":
        _validate_time(spec)
        dom = spec.get("day_of_month")
        if not isinstance(dom, int) or not (1 <= dom <= 28):
            raise ValueError("'day_of_month' debe ser entero entre 1 y 28")

    return spec


def _validate_time(spec: dict) -> None:
    time_str = spec.get("time", "")
    if not time_str or ":" not in time_str:
        raise ValueError(f"Campo 'time' requerido en formato HH:MM, recibido: '{time_str}'")
    parts = time_str.split(":")
    if len(parts) != 2:
        raise ValueError(f"Formato de hora inválido: '{time_str}'. Usar HH:MM")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"Hora fuera de rango: {h}:{m}")


def _parse_time(spec: dict) -> tuple[int, int]:
    parts = spec["time"].split(":")
    return int(parts[0]), int(parts[1])


def compute_next_run(spec: dict, after: datetime | None = None) -> str | None:
    """
    Calcula la próxima ejecución como ISO8601 string.
    Retorna None si no hay más ejecuciones (e.g. 'once' ya pasado).
    """
    if after is None:
        after = now_utc()
    elif after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)

    stype = spec["type"]

    if stype == "once":
        target = parse_iso(spec["at"])
        return to_iso(target) if target > after else None

    if stype == "interval":
        minutes = spec["minutes"]
        return to_iso(after + timedelta(minutes=minutes))

    if stype == "daily":
        h, m = _parse_time(spec)
        candidate = after.replace(hour=h, minute=m, second=0, microsecond=0)
        if candidate <= after:
            candidate += timedelta(days=1)
        return to_iso(candidate)

    if stype == "weekly":
        h, m = _parse_time(spec)
        target_day = _WEEKDAYS[spec["day"].lower()]
        current_day = after.weekday()
        days_ahead = (target_day - current_day) % 7
        candidate = after.replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(days=days_ahead)
        if candidate <= after:
            candidate += timedelta(weeks=1)
        return to_iso(candidate)

    if stype == "monthly":
        h, m = _parse_time(spec)
        dom = spec["day_of_month"]
        candidate = after.replace(day=dom, hour=h, minute=m, second=0, microsecond=0)
        if candidate <= after:
            # Next month
            if after.month == 12:
                candidate = candidate.replace(year=after.year + 1, month=1)
            else:
                candidate = candidate.replace(month=after.month + 1)
        return to_iso(candidate)

    return None


def is_due(next_run_at: str | None, now: datetime | None = None) -> bool:
    if not next_run_at:
        return False
    if now is None:
        now = now_utc()
    try:
        target = parse_iso(next_run_at)
        return target <= now
    except Exception:
        return False


def describe_schedule(spec: dict) -> str:
    stype = spec.get("type", "")

    if stype == "once":
        return f"Una vez: {spec.get('at', '?')}"

    if stype == "interval":
        mins = spec.get("minutes", 0)
        if mins >= 60 and mins % 60 == 0:
            return f"Cada {mins // 60} hora(s)"
        return f"Cada {mins} minutos"

    if stype == "daily":
        return f"Diario a las {spec.get('time', '?')} UTC"

    if stype == "weekly":
        day = spec.get("day", "?").capitalize()
        return f"Semanal: {day} a las {spec.get('time', '?')} UTC"

    if stype == "monthly":
        dom = spec.get("day_of_month", "?")
        return f"Mensual: día {dom} a las {spec.get('time', '?')} UTC"

    return f"Schedule desconocido: {spec}"


def next_aligned_tick(tick_minutes: int, now: datetime | None = None) -> datetime:
    """Próximo tick alineado al reloj (e.g. :00, :05, :10, etc.)."""
    if now is None:
        now = now_utc()
    minute = now.minute
    next_slot = ((minute // tick_minutes) + 1) * tick_minutes
    if next_slot >= 60:
        base = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        extra = next_slot - 60
        return base + timedelta(minutes=extra)
    return now.replace(minute=next_slot, second=0, microsecond=0)
