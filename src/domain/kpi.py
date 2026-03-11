"""Fórmulas de KPI — lógica de negócio pura, sem I/O.

Separada de kpi_engine.py para ser testável sem mock do Supabase.

Regras de negócio:
  - expected = eq_count * svc_count * 3   (3 etapas por serviço: D, R, M)
  - pct = done / expected * 100           (0 se eq_count=0 ou svc_count=0)
  - backlog = max(expected - done, 0)
  - risco_score = (trav*3 + pend*1.5 + andamento*1) / max(total, 1)

Uso:
    from src.domain.kpi import calc_pct, calc_risco, calc_global_kpis
"""
from __future__ import annotations

from typing import TypedDict

import pandas as pd


# ── Tipos de retorno ──────────────────────────────────────────────────────────

class GroupKPI(TypedDict):
    grupo_id:       str
    eq_count:       int
    svc_count:      int
    done_steps:     int
    expected_steps: int
    backlog_steps:  int
    pct:            int


class GlobalKPI(TypedDict):
    pct:            int
    done_steps:     int
    expected_steps: int
    backlog_steps:  int


class RiscoKPI(TypedDict):
    risco_score:    float
    pct_concluido:  float
    pendentes:      int
    travados:       int
    em_andamento:   int
    concluidos:     int
    status_risco:   str   # "alto" | "medio" | "baixo"


_EMPTY_GLOBAL: GlobalKPI = {
    "pct": 0, "done_steps": 0, "expected_steps": 0, "backlog_steps": 0
}


# ── Funções puras ─────────────────────────────────────────────────────────────

def calc_expected(eq_count: int, svc_count: int) -> int:
    """Calcula o total esperado de etapas para um grupo."""
    return max(int(eq_count) * int(svc_count) * 3, 1)


def calc_pct(eq_count: int, svc_count: int, done: int) -> int:
    """Calcula o percentual de conclusão de um grupo (0–100).

    Retorna 0 se não há equipamentos ou serviços configurados.
    """
    if eq_count <= 0 or svc_count <= 0:
        return 0
    expected = calc_expected(eq_count, svc_count)
    raw = int(round(done / expected * 100))
    return max(0, min(100, raw))


def calc_backlog(eq_count: int, svc_count: int, done: int) -> int:
    """Calcula etapas pendentes (backlog) de um grupo."""
    expected = calc_expected(eq_count, svc_count)
    return max(expected - int(done), 0)


def build_group_kpi(
    grupo_id: str,
    eq_count: int,
    svc_count: int,
    done_steps: int,
) -> GroupKPI:
    """Monta o dict de KPI para um único grupo."""
    expected = calc_expected(eq_count, svc_count)
    pct = calc_pct(eq_count, svc_count, done_steps)
    backlog = max(expected - done_steps, 0)
    return GroupKPI(
        grupo_id=grupo_id,
        eq_count=int(eq_count),
        svc_count=int(svc_count),
        done_steps=int(done_steps),
        expected_steps=int(expected),
        backlog_steps=int(backlog),
        pct=int(pct),
    )


def calc_global_kpis(df: pd.DataFrame) -> GlobalKPI:
    """Agrega KPIs de todos os grupos ponderando por expected_steps.

    Recebe o DataFrame retornado por kpi_engine.get_group_kpis().
    Filtra grupos sem equipamentos ou serviços configurados.
    """
    if df is None or df.empty:
        return _EMPTY_GLOBAL

    scope = df[(df["eq_count"] > 0) & (df["svc_count"] > 0)].copy()
    if scope.empty:
        return _EMPTY_GLOBAL

    done     = int(scope["done_steps"].sum())
    expected = int(scope["expected_steps"].sum())
    backlog  = int(scope["backlog_steps"].sum())
    pct = int(round(done / expected * 100)) if expected > 0 else 0
    return GlobalKPI(
        pct=max(0, min(100, pct)),
        done_steps=done,
        expected_steps=expected,
        backlog_steps=backlog,
    )


def calc_dept_kpis(df: pd.DataFrame, group_to_dept: dict[str, str]) -> pd.DataFrame:
    """Agrega KPIs de grupos por departamento.

    Retorna DataFrame com colunas:
      departamento_id, pct, done_steps, expected_steps, backlog_steps, grupos
    """
    empty = pd.DataFrame(
        columns=["departamento_id", "pct", "done_steps", "expected_steps", "backlog_steps", "grupos"]
    )
    if df is None or df.empty:
        return empty

    tmp = df.copy()
    tmp["departamento_id"] = tmp["grupo_id"].map(group_to_dept)
    tmp = tmp.dropna(subset=["departamento_id"])
    tmp = tmp[(tmp["eq_count"] > 0) & (tmp["svc_count"] > 0)]
    if tmp.empty:
        return empty

    g = tmp.groupby("departamento_id", dropna=True).agg(
        done_steps=("done_steps", "sum"),
        expected_steps=("expected_steps", "sum"),
        backlog_steps=("backlog_steps", "sum"),
        grupos=("grupo_id", "nunique"),
    ).reset_index()

    g["pct"] = (
        (g["done_steps"] / g["expected_steps"] * 100)
        .round()
        .fillna(0)
        .astype(int)
        .clip(0, 100)
    )
    return g[["departamento_id", "pct", "done_steps", "expected_steps", "backlog_steps", "grupos"]]


def calc_risco(
    travados: int,
    pendentes: int,
    em_andamento: int,
    concluidos: int,
    total: int,
    pct_concluido: float,
) -> RiscoKPI:
    """Calcula o score e nível de risco de uma revisão.

    Pesos: travado=3.0, pendente=1.5, em_andamento=1.0
    Níveis: alto >= 1.8 | medio >= 0.9 | baixo < 0.9
    """
    if total <= 0:
        return RiscoKPI(
            risco_score=0.0,
            pct_concluido=0.0,
            pendentes=0,
            travados=0,
            em_andamento=0,
            concluidos=0,
            status_risco="baixo",
        )

    score = round(
        (travados * 3.0 + pendentes * 1.5 + em_andamento * 1.0) / total,
        2,
    )
    if score >= 1.8:
        status = "alto"
    elif score >= 0.9:
        status = "medio"
    else:
        status = "baixo"

    return RiscoKPI(
        risco_score=score,
        pct_concluido=float(pct_concluido),
        pendentes=int(pendentes),
        travados=int(travados),
        em_andamento=int(em_andamento),
        concluidos=int(concluidos),
        status_risco=status,
    )


def count_etapas(tarefa: dict) -> int:
    """Conta etapas concluídas (D, R, M) de um registro de tarefa_servico."""
    return (
        int(bool(tarefa.get("etapa_d")))
        + int(bool(tarefa.get("etapa_r")))
        + int(bool(tarefa.get("etapa_m")))
    )
