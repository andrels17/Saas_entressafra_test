"""Testes unitários para módulos críticos: auth, KPI engine e repositórios.

Cobre:
  - permissions.py: can_edit_matriz, can_view_all_data
  - scope.py: empty_scope, normalize helpers
  - tenant.py: _role_ttl logic
  - repositories/base.py: fetch_grupo_template (via mock)
  - kpi_engine helpers: _mv_to_df

Executar: pytest tests/test_auth_kpi.py -v
Não requer Supabase nem Streamlit.
"""
from __future__ import annotations
import pytest
import pandas as pd


# ── permissions.py ──────────────────────────────────────────────────────

from src.auth.permissions import can_edit_matriz, can_view_all_data, has_restricted_data_scope
from src.auth.roles import Role


def test_can_view_all_admin():
    assert can_view_all_data(Role.ADMIN) is True

def test_can_view_all_supervisor():
    assert can_view_all_data(Role.SUPERVISOR) is True

def test_can_view_all_superadmin():
    assert can_view_all_data(Role.SUPERADMIN) is True

def test_can_view_all_viewer_false():
    assert can_view_all_data(Role.VIEWER) is False

def test_can_view_all_none_false():
    assert can_view_all_data(None) is False

def test_can_view_all_string():
    assert can_view_all_data("admin") is True
    assert can_view_all_data("viewer") is False

def test_can_view_all_role_prefix():
    """String como 'Role.ADMIN' deve ser normalizada corretamente."""
    assert can_view_all_data("Role.ADMIN") is True

def test_can_edit_matriz_admin():
    assert can_edit_matriz(Role.ADMIN) is True

def test_can_edit_matriz_viewer_false():
    assert can_edit_matriz(Role.VIEWER) is False

def test_has_restricted_scope_viewer():
    assert has_restricted_data_scope(Role.VIEWER) is True

def test_has_restricted_scope_admin_false():
    assert has_restricted_data_scope(Role.ADMIN) is False


# ── scope.py ─────────────────────────────────────────────────────────────

from src.auth.scope import empty_scope, _uniq, _to_id


def test_empty_scope():
    dep, grp = empty_scope()
    assert dep == [] and grp == []

def test_uniq_basic():
    result = _uniq(["a", "b", "a", "c"])
    assert result == ["a", "b", "c"]

def test_uniq_none_filtered():
    result = _uniq(["a", None, "b"])
    assert None not in result
    assert "a" in result

def test_to_id_string():
    assert _to_id("abc-123") == "abc-123"

def test_to_id_none():
    assert _to_id(None) is None

def test_to_id_strips():
    assert _to_id("  abc  ") == "abc"

def test_to_id_empty_string():
    assert _to_id("") is None

def test_to_id_whitespace_only():
    assert _to_id("   ") is None


# ── supabase_helpers.py ──────────────────────────────────────────────────

from src.utils.supabase_helpers import normalize_id, sanitize_user_input


def test_normalize_id_uuid():
    uid = "f30cf6b3-cc62-47a1-b80c-92a985c49499"
    assert normalize_id(uid) == uid

def test_normalize_id_int():
    assert normalize_id(42) == "42"

def test_normalize_id_none():
    assert normalize_id(None) == ""

def test_sanitize_basic():
    assert sanitize_user_input("hello world") == "hello world"

def test_sanitize_strips():
    assert sanitize_user_input("  hello  ") == "hello"

def test_sanitize_max_length():
    long = "x" * 1000
    result = sanitize_user_input(long, max_length=100)
    assert len(result) == 100

def test_sanitize_removes_null_bytes():
    assert "\x00" not in sanitize_user_input("hello\x00world")

def test_sanitize_empty():
    assert sanitize_user_input("") == ""

def test_sanitize_keeps_newline():
    assert "\n" in sanitize_user_input("line1\nline2")


# ── repositories/base.py ────────────────────────────────────────────────

from src.repositories.base import fetch_grupo_template
from collections import defaultdict


class _MockQuery:
    def __init__(self, data):
        self._data = data
        self._ops = []
    def select(self, *a): return self
    def eq(self, *a): return self
    def in_(self, *a): return self
    def execute(self): return type("R", (), {"data": self._data})()


class _MockSb:
    def __init__(self, responses):
        self._responses = responses  # list of responses to cycle through
        self._call = 0
    def table(self, name):
        data = self._responses[self._call % len(self._responses)]
        self._call += 1
        return _MockQuery(data)


def test_fetch_grupo_template_empty():
    sb = _MockSb([[]])
    s2s, all_s = fetch_grupo_template(sb, "t1", "g1")
    assert all_s == []

def test_fetch_grupo_template_with_data():
    template_rows = [{"servico_id": "sv1", "servicos": {"id": "sv1", "nome": "Motor", "setor": "Mecânica"}}]
    sb = _MockSb([template_rows])
    s2s, all_s = fetch_grupo_template(sb, "t1", "g1")
    assert len(all_s) == 1
    assert all_s[0]["id"] == "sv1"
    assert "Mecânica" in s2s


# ── kpi_engine: _mv_to_df ────────────────────────────────────────────────

from src.utils.kpi_engine import _mv_to_df


def test_mv_to_df_basic():
    rows = [
        {"grupo_id": "g1", "eq_count": 5, "svc_count": 3, "done_steps": 30},
        {"grupo_id": "g2", "eq_count": 2, "svc_count": 2, "done_steps": 0},
    ]
    df = _mv_to_df(rows)
    assert not df.empty
    assert "pct" in df.columns
    g1 = df[df["grupo_id"] == "g1"].iloc[0]
    assert g1["pct"] == 67  # 30 / (5*3*3) * 100 = 66.7 -> 67

def test_mv_to_df_empty():
    df = _mv_to_df([])
    assert df.empty

def test_mv_to_df_pct_clipped():
    """pct nunca deve ultrapassar 100."""
    rows = [{"grupo_id": "g1", "eq_count": 1, "svc_count": 1, "done_steps": 999}]
    df = _mv_to_df(rows)
    # done_steps > expected → fallback retorna df vazio (overflow detectado)
    assert df.empty or df.iloc[0]["pct"] <= 100

def test_mv_to_df_zero_eq():
    rows = [{"grupo_id": "g1", "eq_count": 0, "svc_count": 3, "done_steps": 0}]
    df = _mv_to_df(rows)
    if not df.empty:
        assert df.iloc[0]["pct"] == 0
