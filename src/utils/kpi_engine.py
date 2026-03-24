"""Motor de KPI — orquestra queries (repositório) + fórmulas (domínio).

Melhorias v2:
  - TTL de cache inteligente: 300s para revisões concluídas, 60s para ativas
  - Erros no chunk de tarefas_servico agora são logados (não silenciados)
  - Exposição de invalidate_kpi_cache() para forçar refresh após apontamentos

Interface pública preservada: get_group_kpis, global_kpis, dept_kpis
"""
from __future__ import annotations

import logging
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
from src.utils.observability import log_error
from src.utils.supabase_helpers import sb_for_user
from src.db.supabase_client import get_supabase_anon

log = logging.getLogger("saas.kpi_engine")

# Re-exporta funções de domínio para compatibilidade retroativa
global_kpis = calc_global_kpis
dept_kpis = calc_dept_kpis

# TTLs diferenciados por status da revisão
_TTL_ACTIVE = 60    # revisão em andamento: dados mudam frequentemente
_TTL_CONCLUDED = 3600  # revisão concluída: dados estáticos, cache longo



def _sb_from_token(token: str = ""):
    """Constrói cliente Supabase a partir de um token explícito.
    
    Usado dentro de funções @st.cache_data onde acessar st.session_state
    diretamente causa TypeError em versões recentes do Streamlit.
    """
    sb = get_supabase_anon()
    if token:
        sb.postgrest.auth(token)
    return sb


@st.cache_data(ttl=120, show_spinner=False)
def _get_revisao_status(tenant_id: str, revisao_id: str, _token: str = "") -> str:
    """Busca o status da revisão para decidir o TTL de cache.

    Cacheado por 120s para evitar uma query extra a cada get_group_kpis.
    Retorna "ativa" (default seguro) ou "concluida".
    """
    try:
        sb = _sb_from_token(_token)
        rows = safe_select(
            sb, "revisoes", "status",
            tenant_id__eq=tenant_id, id__eq=revisao_id,
        )
        return (rows[0].get("status") or "ativa") if rows else "ativa"
    except Exception as exc:
        log_error(
            exc,
            context="kpi_engine._get_revisao_status",
            table="revisoes")
        return "ativa"


def invalidate_kpi_cache() -> None:
    """Força invalidação do cache de KPIs na sessão atual.

    Chame após salvar um apontamento para garantir que o próximo
    carregamento leia dados frescos do banco.

    Uso:
        from src.utils.kpi_engine import invalidate_kpi_cache
        invalidate_kpi_cache()
    """
    for fn in (get_group_kpis, _get_group_kpis_concluded, _get_revisao_status):
        try:
            fn.clear()
        except Exception:
            pass  # cache pode não estar inicializado
    # Incrementa o 'ver' na sessão — força nova chave de cache mesmo sem
    # clear()
    ver = st.session_state.get("_kpi_ver", 0)
    st.session_state["_kpi_ver"] = ver + 1
    log.info("Cache de KPIs invalidado (ver=%d)", ver + 1)


def _fetch_mv(tenant_id: str, revisao_id: str, _token: str = "") -> list[dict]:
    sb = _sb_from_token(_token)
    return safe_select(
        sb, "mv_revisao_grupo_kpis", "grupo_id,eq_count,svc_count,done_steps",
        tenant_id__eq=tenant_id, revisao_id__eq=revisao_id,
    )


def _mv_to_df(mv_rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(mv_rows)
    for col in ["eq_count", "svc_count", "done_steps"]:
        df[col] = pd.to_numeric(
            df.get(
                col,
                0),
            errors="coerce").fillna(0).astype(int)

    df["expected_raw"] = (df["eq_count"] * df["svc_count"] * 3).astype(int)
    bad = (df["expected_raw"] > 0) & (df["done_steps"] > df["expected_raw"])
    if bool(bad.any()):
        log.warning(
            "mv_revisao_grupo_kpis retornou done_steps > expected para revisao_id=%s — fallback para raw",
            "?",
        )
        return pd.DataFrame()

    df["expected_steps"] = df["expected_raw"].clip(lower=0).astype(int)
    df["backlog_steps"] = (
        df["expected_steps"] -
        df["done_steps"]).clip(
        lower=0).astype(int)
    df["pct"] = 0
    mask = (
        df["eq_count"] > 0) & (
        df["svc_count"] > 0) & (
            df["expected_steps"] > 0)
    df.loc[mask, "pct"] = ((df.loc[mask, "done_steps"] /
                            df.loc[mask, "expected_steps"] *
                            100).round().astype(int))
    df["pct"] = df["pct"].clip(0, 100).astype(int)
    return df[["grupo_id", "eq_count", "svc_count",
               "done_steps", "expected_steps", "backlog_steps", "pct"]]


def _compute_from_raw(tenant_id: str, revisao_id: str, _token: str = "") -> pd.DataFrame:
    sb = _sb_from_token(_token)
    EMPTY = pd.DataFrame(
        columns=[
            "grupo_id",
            "eq_count",
            "svc_count",
            "done_steps",
            "expected_steps",
            "backlog_steps",
            "pct"])

    grupos = safe_select(
        sb,
        "equip_grupos",
        "id",
        tenant_id__eq=tenant_id,
        ativo__eq=True)
    gids = [g["id"] for g in grupos if g.get("id")]
    if not gids:
        return EMPTY

    eq_rows = safe_select(
        sb,
        "equipamentos",
        "id,grupo_id",
        tenant_id__eq=tenant_id,
        ativo__eq=True,
        grupo_id__in=gids)
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

    # Exclui equipamentos ocultos nesta revisão
    if revisao_id:
        try:
            from src.utils.eq_oculto import get_ocultos
            ocultos = get_ocultos(sb, tenant_id, revisao_id)
            if ocultos:
                for gid in list(grp_to_eq.keys()):
                    grp_to_eq[gid] = [e for e in grp_to_eq[gid] if e not in ocultos]
                eq_to_gid = {e: g for e, g in eq_to_gid.items() if e not in ocultos}
        except Exception:
            pass  # fallback seguro — inclui todos

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
            except Exception as exc:
                log_error(
                    exc,
                    context="kpi_engine._compute_from_raw.chunk",
                    table="tarefas_servico",
                    extra={"chunk_index": i, "chunk_size": len(chunk)},
                )
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


@st.cache_data(ttl=_TTL_ACTIVE, show_spinner=False)
def get_group_kpis(
    tenant_id: str,
    revisao_id: str,
    ver: str = "0",
    prefer_mv: bool = True,
    _token: str = "",
) -> pd.DataFrame:
    """Single source of truth para KPIs de grupo (Matriz & Home).

    TTL adaptativo: usa _TTL_CONCLUDED (1h) para revisões concluídas,
    _TTL_ACTIVE (60s) para revisões em andamento.

    O parâmetro `ver` pode ser incrementado via invalidate_kpi_cache()
    para forçar recarregamento sem esperar o TTL expirar.
    """
    # Ajusta TTL dinamicamente consultando o status da revisão.
    # Revisões concluídas não mudam — podemos cache por muito mais tempo.
    status = _get_revisao_status(tenant_id, revisao_id, _token)
    if status in ("concluida", "encerrada", "fechada"):
        return _get_group_kpis_concluded(tenant_id, revisao_id, ver, _token)

    if prefer_mv:
        mv_rows = _fetch_mv(tenant_id, revisao_id, _token)
        if mv_rows:
            df = _mv_to_df(mv_rows)
            if not df.empty:
                return df
    return _compute_from_raw(tenant_id, revisao_id, _token)


@st.cache_data(ttl=_TTL_CONCLUDED, show_spinner=False)
def _get_group_kpis_concluded(
    tenant_id: str,
    revisao_id: str,
    ver: str = "0",
    _token: str = "",
) -> pd.DataFrame:
    """Variante com TTL longo (1h) para revisões concluídas."""
    mv_rows = _fetch_mv(tenant_id, revisao_id, _token)
    if mv_rows:
        df = _mv_to_df(mv_rows)
        if not df.empty:
            return df
    return _compute_from_raw(tenant_id, revisao_id, _token)
