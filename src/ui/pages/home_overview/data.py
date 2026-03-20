"""Home Overview — camada de dados."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.utils.supabase_helpers import sb_for_user


@st.cache_data(ttl=60, show_spinner=False)
def load_revision(
        tenant_id: str,
        ver: str = "0",
        rev_id: str | None = None) -> dict | None:
    sb = sb_for_user()
    try:
        revs = (
            sb.table("revisoes")
            .select("id,titulo,data_inicio,data_fim,semanas_total,status,created_at,updated_at")
            .eq("tenant_id", tenant_id)
            .order("data_inicio", desc=True)
            .limit(25)
            .execute()
            .data
        ) or []
    except Exception:
        return None
    if not revs:
        return None
    for r in revs:
        if str(
            r.get(
                "status",
                "")).lower() in (
            "ativa",
            "em_andamento",
            "andamento",
            "aberta",
                "open"):
            return r
    return revs[0]


@st.cache_data(ttl=60, show_spinner=False)
def load_groups(tenant_id: str, ver: str = "0") -> list[dict]:
    return (
        sb_for_user()
        .table("equip_grupos")
        .select("id,nome,departamento_id,ativo")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .execute()
        .data
    ) or []


@st.cache_data(ttl=60, show_spinner=False)
def load_depts(tenant_id: str, ver: str = "0") -> list[dict]:
    return (
        sb_for_user()
        .table("departamentos")
        .select("id,nome,ativo")
        .eq("tenant_id", tenant_id)
        .execute()
        .data
    ) or []


@st.cache_data(ttl=60, show_spinner=False)
def load_snapshots(
        tenant_id: str,
        revisao_id: str,
        ver: str = "0") -> pd.DataFrame:
    sb = sb_for_user()
    try:
        rows = (
            sb.table("kpi_snapshots")
            .select("week_number,grupo_id,pct,done_steps,expected_steps,backlog_steps,created_at")
            .eq("tenant_id", tenant_id)
            .eq("revisao_id", revisao_id)
            .order("week_number")
            .limit(20_000)  # usar fetch_all() se exceder este limite
            .execute()
            .data
        ) or []
    except Exception:
        rows = []
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    for c in [
        "week_number",
        "pct",
        "done_steps",
        "expected_steps",
            "backlog_steps"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
    return df


@st.cache_data(ttl=60, show_spinner=False)
def load_group_sector_view(
        tenant_id: str,
        revisao_id: str,
        ver: str = "0") -> dict:
    sb = sb_for_user()
    try:
        rows = (
            sb.table("vw_revisao_grupo_setores")
            .select("grupo_id,setores_total,setores_concluidos,setores_pendentes")
            .eq("tenant_id", tenant_id)
            .eq("revisao_id", revisao_id)
            .execute()
            .data
        ) or []
    except Exception:
        rows = []
    return {
        str(r["grupo_id"]): {
            "setores_total": int(r.get("setores_total") or 0),
            "setores_concluidos": int(r.get("setores_concluidos") or 0),
            "setores_pendentes": int(r.get("setores_pendentes") or 0),
        }
        for r in rows if r.get("grupo_id")
    }


@st.cache_data(ttl=300, show_spinner=False)
def snapshots_supported(tenant_id: str = "", ver: str = "0") -> bool:
    """Verifica se a tabela kpi_snapshots existe — cacheado por 5 minutos."""
    try:
        sb_for_user().table("kpi_snapshots").select("id").limit(1).execute()
        return True
    except Exception:
        return False


def insert_snapshot(tenant_id: str, revisao_id: str,
                    week_number: int, df: pd.DataFrame) -> tuple[bool, str]:
    if df is None or df.empty:
        return False, "Sem dados"
    sb = sb_for_user()
    rows = [
        {
            "tenant_id": tenant_id,
            "revisao_id": revisao_id,
            "week_number": int(week_number),
            "grupo_id": r.get("grupo_id"),
            "pct": int(r.get("pct") or 0),
            "done_steps": int(r.get("done_steps") or 0),
            "expected_steps": int(r.get("expected_steps") or 0),
            "backlog_steps": int(r.get("backlog_steps") or 0),
        }
        for r in df.to_dict("records")
    ]
    try:
        sb.table("kpi_snapshots").insert(rows).execute()
        return True, "OK"
    except Exception as e:
        return False, str(e)
