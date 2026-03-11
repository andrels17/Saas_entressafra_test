from __future__ import annotations
from datetime import date


def iso_week(d: date) -> int:
    return int(d.isocalendar().week)


def week_from_revisao(d: date, data_inicio: date | None, semanas_total: int | None) -> int:
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
            # clamp
            if w > semanas_total:
                w = semanas_total
        return int(w)
    return iso_week(d)
