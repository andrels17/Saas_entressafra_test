from __future__ import annotations

from datetime import date, datetime, time, timezone


def iso_week(d: date) -> int:
    return int(d.isocalendar().week)


def week_from_revisao(d: date, data_inicio: date | None,
                      semanas_total: int | None) -> int:
    """
    If data_inicio exists, compute week index starting at 1 within the revision (ceil(days/7)+1).
    Else fallback to ISO week number.
    """
    if data_inicio:
        delta_days = (d - data_inicio).days
        if delta_days < 0:
            return 1
        w = (delta_days // 7) + 1
        if semanas_total and semanas_total > 0:
            if w > semanas_total:
                w = semanas_total
        return int(w)
    return iso_week(d)


def operational_week_start_date(
    semana_operacional: int | None,
    data_inicio: date | None,
    semanas_total: int | None,
) -> date | None:
    """Return the first day of the operational week within the revision."""
    if not data_inicio:
        return None
    try:
        week = int(semana_operacional or 1)
    except Exception:
        week = 1
    week = max(1, week)
    if semanas_total and semanas_total > 0:
        week = min(week, int(semanas_total))
    return data_inicio.fromordinal(data_inicio.toordinal() + ((week - 1) * 7))


def _coerce_date(value) -> date | None:
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).date()
    except Exception:
        return None


def apontamento_datetime_iso(
    *,
    data_apontamento=None,
    semana_operacional: int | None,
    data_inicio: date | None,
    semanas_total: int | None,
) -> str | None:
    """Resolve the datetime to persist for a task event."""
    chosen_date = _coerce_date(data_apontamento)
    if chosen_date is None:
        chosen_date = operational_week_start_date(semana_operacional, data_inicio, semanas_total)
    if chosen_date is None:
        return None
    return datetime.combine(chosen_date, time(hour=12), tzinfo=timezone.utc).isoformat()


def effective_week_for_apontamento(
    *,
    data_apontamento=None,
    semana_operacional: int | None,
    data_inicio: date | None,
    semanas_total: int | None,
) -> int | None:
    """Return the operational week that should be saved with the task."""
    chosen_date = _coerce_date(data_apontamento)
    if chosen_date is not None:
        return int(week_from_revisao(chosen_date, data_inicio, semanas_total))
    try:
        week = int(semana_operacional or 0)
    except Exception:
        week = 0
    if week <= 0:
        week = 1
    if semanas_total and semanas_total > 0:
        week = min(week, int(semanas_total))
    return int(week)
