"""Testes unitários para a lógica da Matriz Operacional.

Cobre:
  - _collect_matrix_changes (page.py)
  - build_sector_frame (matriz_sector.py)
  - bulk_update_tasks (matriz_runtime.py)
  - normalize_id (supabase_helpers.py)

Executar com: pytest tests/test_matriz_logic.py -v
Não requer Supabase nem Streamlit.
"""
from __future__ import annotations

import pandas as pd
import pytest


# ── normalize_id ─────────────────────────────────────────────────────────────

from src.utils.supabase_helpers import normalize_id

def test_normalize_id_string():
    assert normalize_id("abc-123") == "abc-123"

def test_normalize_id_int():
    assert normalize_id(42) == "42"

def test_normalize_id_none():
    assert normalize_id(None) == ""

def test_normalize_id_strips_whitespace():
    assert normalize_id("  abc  ") == "abc"

def test_normalize_id_uuid():
    uid = "f30cf6b3-cc62-47a1-b80c-92a985c49499"
    assert normalize_id(uid) == uid


# ── build_sector_frame ────────────────────────────────────────────────────────

from src.ui.pages.matriz_sector import build_sector_frame

def _make_task(eid, sid, d=False, r=False, m=False, obs=None):
    return {
        "equipamento_id": eid,
        "servico_id": sid,
        "etapa_d": d,
        "etapa_r": r,
        "etapa_m": m,
        "observacao": obs,
        "status": "pendente",
        "semana": None,
        "id": f"{eid}__{sid}",
    }

def test_build_sector_frame_basic():
    eqs = [{"id": "eq1"}, {"id": "eq2"}]
    svc_ids = ["sv1", "sv2"]
    svc_names = ["Serviço A", "Serviço B"]
    task_map = {
        ("eq1", "sv1"): _make_task("eq1", "sv1", d=True),
        ("eq1", "sv2"): _make_task("eq1", "sv2"),
        ("eq2", "sv1"): _make_task("eq2", "sv1", d=True, r=True, m=True),
        ("eq2", "sv2"): _make_task("eq2", "sv2"),
    }
    eq_label_short = {"eq1": "001", "eq2": "002"}

    df, col_meta, obs_map = build_sector_frame(
        equipamentos=eqs,
        svc_ids=svc_ids,
        svc_names=svc_names,
        task_map=task_map,
        eq_label_short=eq_label_short,
    )

    assert len(df) == 2
    assert "Serviço A D" in col_meta
    assert col_meta["Serviço A D"] == ("sv1", "etapa_d")
    assert col_meta["Serviço B M"] == ("sv2", "etapa_m")


def test_build_sector_frame_str_keys():
    """task_map com int keys deve ser encontrado via str() normalization."""
    eqs = [{"id": "eq1"}]
    task_map = {("eq1", "sv1"): _make_task("eq1", "sv1", d=True)}
    eq_label_short = {"eq1": "001"}

    df, col_meta, _ = build_sector_frame(
        equipamentos=eqs,
        svc_ids=["sv1"],
        svc_names=["Svc"],
        task_map=task_map,
        eq_label_short=eq_label_short,
    )
    assert df.iloc[0]["Svc D"] is True


def test_build_sector_frame_empty_tasks():
    """Sem tarefas: todas as etapas False, sem obs."""
    eqs = [{"id": "eq1"}]
    df, col_meta, obs_map = build_sector_frame(
        equipamentos=eqs,
        svc_ids=["sv1"],
        svc_names=["Svc"],
        task_map={},
        eq_label_short={"eq1": "001"},
    )
    assert df.iloc[0]["Svc D"] is False
    assert obs_map == {}


def test_build_sector_frame_obs_map():
    """Observações preenchidas devem aparecer no obs_map."""
    eqs = [{"id": "eq1"}]
    task_map = {("eq1", "sv1"): _make_task("eq1", "sv1", obs="Peça faltando")}
    df, _, obs_map = build_sector_frame(
        equipamentos=eqs,
        svc_ids=["sv1"],
        svc_names=["Svc"],
        task_map=task_map,
        eq_label_short={"eq1": "001"},
    )
    assert "eq1__sv1" in obs_map
    assert obs_map["eq1__sv1"] == "Peça faltando"


# ── _collect_matrix_changes ───────────────────────────────────────────────────

from src.ui.pages.matriz_modular.page import _collect_matrix_changes

def _make_df(equip_ids, svc_cols, values):
    """Helper: cria df_display com index = equip_ids e colunas bool."""
    data = {col: [values[i][j] for i in range(len(equip_ids))]
            for j, col in enumerate(svc_cols)}
    df = pd.DataFrame(data, index=equip_ids)
    return df

def test_collect_no_changes():
    svc_bool = ["Svc D", "Svc R"]
    col_meta = {"Svc D": ("sv1", "etapa_d"), "Svc R": ("sv1", "etapa_r")}
    df = _make_df(["eq1"], svc_bool, [[False, False]])
    edited = _make_df(["eq1"], svc_bool, [[False, False]])
    changes = _collect_matrix_changes(df, edited, svc_bool, col_meta)
    assert changes == []

def test_collect_one_change():
    svc_bool = ["Svc D", "Svc R"]
    col_meta = {"Svc D": ("sv1", "etapa_d"), "Svc R": ("sv1", "etapa_r")}
    df = _make_df(["eq1"], svc_bool, [[False, False]])
    edited = _make_df(["eq1"], svc_bool, [[True, False]])
    changes = _collect_matrix_changes(df, edited, svc_bool, col_meta)
    assert len(changes) == 1
    assert changes[0] == ("eq1", "sv1", "etapa_d", True)

def test_collect_multiple_changes():
    svc_bool = ["Svc D", "Svc R", "Svc M"]
    col_meta = {
        "Svc D": ("sv1", "etapa_d"),
        "Svc R": ("sv1", "etapa_r"),
        "Svc M": ("sv1", "etapa_m"),
    }
    df = _make_df(["eq1", "eq2"], svc_bool, [[False, False, False], [True, True, False]])
    edited = _make_df(["eq1", "eq2"], svc_bool, [[True, True, False], [True, True, True]])
    changes = _collect_matrix_changes(df, edited, svc_bool, col_meta)
    assert len(changes) == 3

def test_collect_ids_normalized_to_str():
    """IDs retornados em changes devem ser str."""
    svc_bool = ["Svc D"]
    col_meta = {"Svc D": ("sv1", "etapa_d")}
    df = _make_df(["eq1"], svc_bool, [[False]])
    edited = _make_df(["eq1"], svc_bool, [[True]])
    changes = _collect_matrix_changes(df, edited, svc_bool, col_meta)
    assert isinstance(changes[0][0], str)
    assert isinstance(changes[0][1], str)

def test_collect_unknown_col_skipped():
    """Colunas não presentes em col_meta devem ser ignoradas sem erro."""
    svc_bool = ["Svc D", "Status"]
    col_meta = {"Svc D": ("sv1", "etapa_d")}  # Status não está em col_meta
    df = _make_df(["eq1"], svc_bool, [[False, False]])
    edited = _make_df(["eq1"], svc_bool, [[True, False]])
    changes = _collect_matrix_changes(df, edited, svc_bool, col_meta)
    assert len(changes) == 1  # só Svc D

def test_collect_none_edited():
    changes = _collect_matrix_changes(pd.DataFrame(), None, [], {})
    assert changes == []


# ── bulk_update_tasks ─────────────────────────────────────────────────────────

from src.ui.pages.matriz_runtime import bulk_update_tasks

class _MockTable:
    def __init__(self):
        self.upserted = []
        self.updated = []
        self._fail_upsert = False

    def upsert(self, rows, **kwargs):
        self.upserted.extend(rows)
        return self

    def update(self, row):
        self.updated.append(row)
        return self

    def eq(self, *args):
        return self

    def execute(self):
        if self._fail_upsert:
            raise Exception("upsert failed")
        return type("R", (), {"data": []})()


class _MockSb:
    def __init__(self, fail_upsert=False):
        self._table = _MockTable()
        self._table._fail_upsert = fail_upsert

    def table(self, name):
        return self._table


def test_bulk_update_empty():
    sb = _MockSb()
    ok, failed = bulk_update_tasks(sb, [])
    assert ok == 0 and failed == 0


def test_bulk_update_success():
    sb = _MockSb()
    updates = [{"id": "t1", "etapa_d": True}, {"id": "t2", "etapa_r": True}]
    ok, failed = bulk_update_tasks(sb, updates)
    assert ok == 2
    assert failed == 0


def test_bulk_update_fallback_on_upsert_failure():
    """Quando upsert falha, deve tentar update individual."""
    sb = _MockSb(fail_upsert=True)
    sb._table._fail_upsert = True
    updates = [{"id": "t1", "etapa_d": True}]
    ok, failed = bulk_update_tasks(sb, updates)
    # Fallback individual update should succeed
    assert ok == 1
    assert failed == 0


def test_bulk_update_missing_id():
    sb = _MockSb(fail_upsert=True)
    updates = [{"etapa_d": True}]  # sem id
    ok, failed = bulk_update_tasks(sb, updates)
    assert failed == 1
    assert ok == 0
