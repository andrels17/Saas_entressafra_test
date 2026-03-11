"""Testes unitários para src/domain/kpi.py.

Executar com: pytest tests/test_kpi.py -v
Não requer Supabase nem Streamlit — apenas pandas.
"""
import pandas as pd
import pytest

from src.domain.kpi import (
    calc_expected,
    calc_pct,
    calc_backlog,
    build_group_kpi,
    calc_global_kpis,
    calc_dept_kpis,
    calc_risco,
    count_etapas,
)


# ── calc_expected ─────────────────────────────────────────────────────────────

def test_expected_normal():
    assert calc_expected(2, 3) == 18  # 2 * 3 * 3

def test_expected_minimo_um():
    assert calc_expected(0, 3) == 1   # clip(lower=1)

def test_expected_sem_servicos():
    assert calc_expected(5, 0) == 1


# ── calc_pct ──────────────────────────────────────────────────────────────────

def test_pct_sem_equipamentos():
    assert calc_pct(0, 3, 0) == 0

def test_pct_sem_servicos():
    assert calc_pct(3, 0, 0) == 0

def test_pct_completo():
    assert calc_pct(2, 3, 18) == 100  # done = expected

def test_pct_metade():
    assert calc_pct(2, 3, 9) == 50

def test_pct_clamped_max():
    assert calc_pct(1, 1, 999) == 100  # nunca > 100

def test_pct_clamped_min():
    assert calc_pct(1, 1, 0) == 0


# ── calc_backlog ──────────────────────────────────────────────────────────────

def test_backlog_zero_quando_completo():
    assert calc_backlog(2, 3, 18) == 0

def test_backlog_total_quando_vazio():
    assert calc_backlog(2, 3, 0) == 18  # 2*3*3

def test_backlog_nunca_negativo():
    assert calc_backlog(1, 1, 999) == 0  # done > expected → clip(0)


# ── build_group_kpi ───────────────────────────────────────────────────────────

def test_build_group_kpi_completo():
    kpi = build_group_kpi("g1", 2, 3, 18)
    assert kpi["pct"]            == 100
    assert kpi["backlog_steps"]  == 0
    assert kpi["expected_steps"] == 18
    assert kpi["grupo_id"]       == "g1"

def test_build_group_kpi_vazio():
    kpi = build_group_kpi("g2", 0, 3, 0)
    assert kpi["pct"] == 0


# ── calc_global_kpis ──────────────────────────────────────────────────────────

def test_global_kpis_dataframe_vazio():
    result = calc_global_kpis(pd.DataFrame())
    assert result["pct"] == 0
    assert result["done_steps"] == 0

def test_global_kpis_ponderado():
    df = pd.DataFrame([
        {"grupo_id": "a", "eq_count": 2, "svc_count": 1, "done_steps": 6, "expected_steps": 6,  "backlog_steps": 0,  "pct": 100},
        {"grupo_id": "b", "eq_count": 2, "svc_count": 1, "done_steps": 0, "expected_steps": 6,  "backlog_steps": 6,  "pct": 0},
    ])
    result = calc_global_kpis(df)
    assert result["pct"] == 50
    assert result["done_steps"] == 6
    assert result["expected_steps"] == 12

def test_global_kpis_ignora_sem_peso():
    df = pd.DataFrame([
        {"grupo_id": "a", "eq_count": 0, "svc_count": 3, "done_steps": 0, "expected_steps": 1, "backlog_steps": 1, "pct": 0},
    ])
    result = calc_global_kpis(df)
    assert result["pct"] == 0


# ── calc_dept_kpis ────────────────────────────────────────────────────────────

def test_dept_kpis_agrupa_corretamente():
    df = pd.DataFrame([
        {"grupo_id": "g1", "eq_count": 1, "svc_count": 1, "done_steps": 3,  "expected_steps": 3, "backlog_steps": 0, "pct": 100},
        {"grupo_id": "g2", "eq_count": 1, "svc_count": 1, "done_steps": 0,  "expected_steps": 3, "backlog_steps": 3, "pct": 0},
        {"grupo_id": "g3", "eq_count": 1, "svc_count": 1, "done_steps": 3,  "expected_steps": 3, "backlog_steps": 0, "pct": 100},
    ])
    mapping = {"g1": "dept_a", "g2": "dept_a", "g3": "dept_b"}
    result  = calc_dept_kpis(df, mapping)

    dept_a = result[result["departamento_id"] == "dept_a"].iloc[0]
    dept_b = result[result["departamento_id"] == "dept_b"].iloc[0]

    assert dept_a["pct"]    == 50    # (3 + 0) / (3 + 3) * 100
    assert dept_b["pct"]    == 100
    assert dept_a["grupos"] == 2
    assert dept_b["grupos"] == 1

def test_dept_kpis_dataframe_vazio():
    result = calc_dept_kpis(pd.DataFrame(), {})
    assert result.empty


# ── calc_risco ────────────────────────────────────────────────────────────────

def test_risco_alto():
    r = calc_risco(travados=10, pendentes=0, em_andamento=0, concluidos=0, total=10, pct_concluido=0.0)
    assert r["status_risco"]  == "alto"
    assert r["risco_score"]   == 3.0

def test_risco_baixo():
    r = calc_risco(travados=0, pendentes=0, em_andamento=0, concluidos=10, total=10, pct_concluido=100.0)
    assert r["status_risco"] == "baixo"
    assert r["risco_score"]  == 0.0

def test_risco_medio():
    r = calc_risco(travados=0, pendentes=6, em_andamento=0, concluidos=4, total=10, pct_concluido=40.0)
    assert r["status_risco"] == "medio"

def test_risco_total_zero():
    r = calc_risco(travados=0, pendentes=0, em_andamento=0, concluidos=0, total=0, pct_concluido=0.0)
    assert r["risco_score"]  == 0.0
    assert r["status_risco"] == "baixo"


# ── count_etapas ──────────────────────────────────────────────────────────────

def test_count_etapas_todas():
    assert count_etapas({"etapa_d": True, "etapa_r": True,  "etapa_m": True})  == 3

def test_count_etapas_nenhuma():
    assert count_etapas({"etapa_d": False,"etapa_r": False, "etapa_m": False}) == 0

def test_count_etapas_parcial():
    assert count_etapas({"etapa_d": True, "etapa_r": False, "etapa_m": True})  == 2

def test_count_etapas_campos_ausentes():
    assert count_etapas({}) == 0
