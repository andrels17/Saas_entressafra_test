from __future__ import annotations

import importlib.util
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


weeks_mod = _load_module("weeks_regression", "src/utils/weeks.py")
tz = _load_module("timezone_regression", "src/utils/timezone.py")


def test_week_from_revisao_clamps_before_start_and_after_end():
    inicio = date(2026, 1, 10)

    assert weeks_mod.week_from_revisao(date(2026, 1, 1), inicio, 4) == 1
    assert weeks_mod.week_from_revisao(date(2026, 1, 10), inicio, 4) == 1
    assert weeks_mod.week_from_revisao(date(2026, 1, 17), inicio, 4) == 2
    assert weeks_mod.week_from_revisao(date(2026, 3, 30), inicio, 4) == 4


def test_semana_da_revisao_uses_brt_calendar_boundaries(monkeypatch):
    monkeypatch.setattr(
        tz,
        "now_brt",
        lambda: datetime(2026, 1, 13, 0, 30, tzinfo=tz.BRT),
    )

    assert tz.semana_da_revisao("2026-01-06", 8) == 2
    assert tz.semana_da_revisao("2026-01-06T03:00:00Z", 8) == 2


def test_semana_da_revisao_invalid_input_falls_back_to_one():
    assert tz.semana_da_revisao(None, 6) == 1
    assert tz.semana_da_revisao("data-invalida", 6) == 1
