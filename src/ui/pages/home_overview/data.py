"""Home Overview — camada de dados."""
from __future__ import annotations

import hashlib
import pandas as pd
import streamlit as st

from src.db.supabase_client import get_supabase_anon
from src.utils.observability import log_error


def _sb_from_token(token: str = ""):
    """Constrói cliente Supabase autenticado a partir de um token explícito."""
    sb = get_supabase_anon()
    if token:
        try:
            sb.postgrest.auth(token)
        except Exception:
            pass
    return sb


def _token_hash(token: str) -> str:
    """Retorna hash curto do token para usar como chave de cache segura.

    Incluir o hash (não o token bruto) na chave garante que sessões de
    usuários diferentes nunca compartilhem o mesmo resultado cacheado,
    sem expor o JWT nos logs do Streamlit.
    """
    return hashlib.md5((token or "").encode()).hexdigest()[:8]


@st.cache_data(ttl=60, show_spinner=False)
def load_revision(
        tenant_id: str,
        ver: str = "0",
        rev_id: str | None = None,
        _token: str = "") -> dict | None:
    # load_revision não filtra por RLS de usuário — token só garante autenticação
    sb = _sb_from_token(_token)
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
    except Exception as exc:
        log_error(exc, context="home_overview.load_revision", table="revisoes")
        return None

    rev_id = str(rev_id or "").strip()
    if rev_id:
        match = next((r for r in revs if str(r.get("id")) == rev_id), None)
        if match:
            return match

    for r in revs:
        if str(r.get("status", "")).lower() in (
            "ativa", "em_andamento", "andamento", "aberta", "open"
        ):
            return r
    return revs[0] if revs else None


@st.cache_data(ttl=60, show_spinner=False)
def load_groups(tenant_id: str, ver: str = "0", token_hash: str = "", _token: str = "") -> list[dict]:
    """Carrega grupos do tenant.

    `token_hash` é o md5[:8] do JWT e faz parte da chave de cache,
    garantindo que sessões de usuários diferentes não compartilhem dados.
    `_token` (underscore) é excluído do cache key pelo Streamlit e serve
    apenas para autenticar o cliente Supabase.
    """
    try:
        return (
            _sb_from_token(_token)
            .table("equip_grupos")
            .select("id,nome,departamento_id,ativo")
            .eq("tenant_id", tenant_id)
            .eq("ativo", True)
            .execute()
            .data
        ) or []
    except Exception as exc:
        log_error(exc, context="home_overview.load_groups", table="equip_grupos")
        return []


@st.cache_data(ttl=60, show_spinner=False)
def load_depts(tenant_id: str, ver: str = "0", token_hash: str = "", _token: str = "") -> list[dict]:
    """Carrega departamentos do tenant.

    `token_hash` é o md5[:8] do JWT e faz parte da chave de cache,
    garantindo que sessões de usuários diferentes não compartilhem dados.
    """
    try:
        return (
            _sb_from_token(_token)
            .table("departamentos")
            .select("id,nome,ativo")
            .eq("tenant_id", tenant_id)
            .execute()
            .data
        ) or []
    except Exception as exc:
        log_error(exc, context="home_overview.load_depts", table="departamentos")
        return []


@st.cache_data(ttl=60, show_spinner=False)
def load_snapshots(
        tenant_id: str,
        revisao_id: str,
        ver: str = "0",
        _token: str = "") -> pd.DataFrame:
    sb = _sb_from_token(_token)
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
    except Exception as exc:
        log_error(exc, context="home_overview.load_snapshots", table="kpi_snapshots")
        rows = []

    df = pd.DataFrame(rows)
    for c in ["week_number", "pct", "done_steps", "expected_steps", "backlog_steps"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
    return df


@st.cache_data(ttl=60, show_spinner=False)
def load_group_sector_view(
        tenant_id: str,
        revisao_id: str,
        ver: str = "0",
        _token: str = "") -> dict:
    sb = _sb_from_token(_token)
    try:
        rows = (
            sb.table("vw_revisao_grupo_setores")
            .select("grupo_id,setores_total,setores_concluidos,setores_pendentes")
            .eq("tenant_id", tenant_id)
            .eq("revisao_id", revisao_id)
            .execute()
            .data
        ) or []
    except Exception as exc:
        log_error(exc, context="home_overview.load_group_sector_view",
                  table="vw_revisao_grupo_setores")
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
def snapshots_supported(tenant_id: str = "", ver: str = "0", _token: str = "") -> bool:
    """Verifica se a tabela kpi_snapshots existe — cacheado por 5 minutos."""
    try:
        _sb_from_token(_token).table("kpi_snapshots").select("id").limit(1).execute()
        return True
    except Exception:
        return False


def insert_snapshot(tenant_id: str, revisao_id: str,
                    week_number: int, df: pd.DataFrame) -> tuple[bool, str]:
    # insert_snapshot não é cacheada — pode acessar session_state diretamente
    from src.utils.supabase_helpers import sb_for_user
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
