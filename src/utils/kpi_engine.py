"""Motor de KPI — orquestra queries (repositório) + fórmulas (domínio).

Refatorado para:
  - Delegar fórmulas para src.domain.kpi (testável sem Supabase)
  - Delegar queries para src.repositories.base.safe_select
  - Manter a interface pública inalterada (get_group_kpis, global_kpis, dept_kpis)
    para compatibilidade com os módulos que já a importam.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import pandas as pd
import streamlit as st

from src.domain.kpi import (
    build_group_kpi,
    calc_global_kpis,
    calc_dept_kpis,
    count_etapas,
)
from src.repositories.base import safe_select
from src.utils.supabase_helpers import sb_for_user

# Re-exporta funções de domínio para compatibilidade retroativa
global_kpis = calc_global_kpis
dept_kpis   = calc_dept_kpis


def _fetch_mv(tenant_id: str, revisao_id: str) -> list[dict]:
    sb = sb_for_user()
    return safe_select(
        sb, "mv_revisao_grupo_kpis", "grupo_id,eq_count,svc_count,done_steps",
        tenant_id__eq=tenant_id, revisao_id__eq=revisao_id,
    )


def _mv_to_df(mv_rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(mv_rows)
    for col in ["eq_count", "svc_count", "done_steps"]:
        df[col] = pd.to_numeric(df.get(col, 0), errors="coerce").fillna(0).astype(int)

    df["expected_raw"] = (df["eq_count"] * df["svc_count"] * 3).astype(int)
    bad = (df["expected_raw"] > 0) & (df["done_steps"] > df["expected_raw"])
    if bool(bad.any()):
        return pd.DataFrame()

    df["expected_steps"] = df["expected_raw"].clip(lower=1).astype(int)
    df["backlog_steps"] = (df["expected_steps"] - df["done_steps"]).clip(lower=0).astype(int)
    df["pct"] = 0
    mask = (df["eq_count"] > 0) & (df["svc_count"] > 0) & (df["expected_steps"] > 0)
    df.loc[mask, "pct"] = (
        (df.loc[mask, "done_steps"] / df.loc[mask, "expected_steps"] * 100).round().astype(int)
    )
    df["pct"] = df["pct"].clip(0, 100).astype(int)
    return df[["grupo_id", "eq_count", "svc_count", "done_steps", "expected_steps", "backlog_steps", "pct"]]


def _compute_from_raw(tenant_id: str, revisao_id: str) -> pd.DataFrame:
    sb = sb_for_user()
    EMPTY = pd.DataFrame(columns=["grupo_id","eq_count","svc_count","done_steps","expected_steps","backlog_steps","pct"])

    grupos = safe_select(sb, "equip_grupos", "id", tenant_id__eq=tenant_id, ativo__eq=True)
    gids = [g["id"] for g in grupos if g.get("id")]
    if not gids:
        return EMPTY

    eq_rows = safe_select(sb, "equipamentos", "id,grupo_id",
                          tenant_id__eq=tenant_id, ativo__eq=True, grupo_id__in=gids)
    grp_to_eq: dict[str, list[str]] = defaultdict(list)
    eq_to_gid: dict[str, str] = {}
    for r in eq_rows:
        gid, eid = r.get("grupo_id"), r.get("id")
        if gid and eid:
            grp_to_eq[gid].append(eid)
            eq_to_gid[eid] = gid

    tpl_rows = safe_select(sb, "grupo_servicos", "grupo_id,servico_id",
                           tenant_id__eq=tenant_id, grupo_id__in=gids)
    grp_to_services: dict[str, set[str]] = defaultdict(set)
    for r in tpl_rows:
        gid, sid = r.get("grupo_id"), r.get("servico_id")
        if gid and sid:
            grp_to_services[gid].add(sid)

    all_eq_ids = list(eq_to_gid.keys())
    done_by_gid: dict[str, int] = defaultdict(int)
    if all_eq_ids and revisao_id:
        for i in range(0, len(all_eq_ids), 500):
            chunk = all_eq_ids[i: i + 500]
            try:
                trows = (
                    sb.table("tarefas_servico")
                    .select("equipamento_id,etapa_d,etapa_r,etapa_m")
                    .eq("tenant_id", tenant_id).eq("revisao_id", revisao_id)
                    .in_("equipamento_id", chunk).execute().data
                ) or []
            except Exception:
                trows = []
            for t in trows:
                eid = t.get("equipamento_id")
                gid = eq_to_gid.get(eid)
                if gid:
                    done_by_gid[gid] += count_etapas(t)

    rows: list[dict[str, Any]] = [
        build_group_kpi(
            grupo_id=gid,
            eq_count=len(grp_to_eq.get(gid) or []),
            svc_count=len(grp_to_services.get(gid) or set()),
            done_steps=int(done_by_gid.get(gid, 0)),
        )
        for gid in gids
    ]
    return pd.DataFrame(rows)


@st.cache_data(ttl=60, show_spinner=False)
def get_group_kpis(
    tenant_id: str,
    revisao_id: str,
    ver: str = "0",
    prefer_mv: bool = True,
) -> pd.DataFrame:
    """Single source of truth para KPIs de grupo (Matriz & Home)."""
    if prefer_mv:
        mv_rows = _fetch_mv(tenant_id, revisao_id)
        if mv_rows:
            df = _mv_to_df(mv_rows)
            if not df.empty:
                return df
    return _compute_from_raw(tenant_id, revisao_id)
