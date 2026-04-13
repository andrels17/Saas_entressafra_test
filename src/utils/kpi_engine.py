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
from src.repositories.base import safe_select, safe_select_paginated
from src.utils.observability import log_error
from src.utils.supabase_helpers import sb_for_user
from src.db.supabase_client import get_supabase_anon, get_supabase_service

log = logging.getLogger("saas.kpi_engine")

# Re-exporta funções de domínio para compatibilidade retroativa
global_kpis = calc_global_kpis
dept_kpis = calc_dept_kpis

# TTLs diferenciados por status da revisão
_TTL_ACTIVE = 60    # revisão em andamento: dados mudam frequentemente
_TTL_CONCLUDED = 3600  # revisão concluída: dados estáticos, cache longo



def _sb_from_token(token: str = ""):
    """Constrói cliente Supabase a partir de um token explícito.

    Preferimos service-role para leituras consolidadas do dashboard/matriz,
    pois alguns perfis (gestor/supervisor) podem ficar com SELECT bloqueado por
    RLS em tarefas/views e acabar vendo 0%. O recorte final continua sendo
    aplicado na camada de aplicação via escopo do usuário.
    """
    try:
        return get_supabase_service()
    except Exception:
        sb = get_supabase_anon()
        if token:
            try:
                sb.postgrest.auth(token)
            except Exception:
                pass
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
    # Incrementa _kpi_ver: a lógica de get_group_kpis usa este valor para
    # decidir se deve ignorar mv_revisao_grupo_kpis e ir direto ao raw.
    # Isso garante que após um apontamento os dados sejam sempre recarregados
    # do banco, independente do estado da view materializada.
    ver = st.session_state.get("_kpi_ver", 0)
    new_ver = ver + 1
    st.session_state["_kpi_ver"] = new_ver
    # Sincroniza também data_version para que caches dependentes (dashboard,
    # home) recebam a nova chave e não sirvam dados antigos.
    try:
        data_ver = st.session_state.get("data_version", "0")
        st.session_state["data_version"] = str(int(float(data_ver)) + 1)
    except Exception:
        st.session_state["data_version"] = str(new_ver)
    log.info("Cache de KPIs invalidado (kpi_ver=%d)", new_ver)


def _fetch_mv(tenant_id: str, revisao_id: str, _token: str = "") -> list[dict]:
    sb = _sb_from_token(_token)
    return safe_select_paginated(
        sb, "mv_revisao_grupo_kpis", "grupo_id,eq_count,svc_count,done_steps",
        tenant_id__eq=tenant_id, revisao_id__eq=revisao_id,
    )


def _mv_to_df(mv_rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(mv_rows)
    if df.empty:
        return pd.DataFrame(columns=["grupo_id", "eq_count", "svc_count", "done_steps", "expected_steps", "backlog_steps", "pct"])
    for col in ["eq_count", "svc_count", "done_steps"]:
        source = df[col] if col in df.columns else pd.Series([0] * len(df), index=df.index)
        df[col] = pd.to_numeric(source, errors="coerce").fillna(0).astype(int)

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

    # Busca TODOS os grupos (sem filtro ativo) para garantir que equipamentos
    # vinculados a grupos inativos sejam contabilizados corretamente.
    # O filtro ativo=True causava eq_count=0 quando equipamentos apontavam
    # para grupos com ativo=False, zerando todos os KPIs da Home.
    # Usa paginação: tenants com muitos grupos ultrapassariam o limite de 1000
    # linhas do Supabase, causando truncamento silencioso dos gids.
    grupos = safe_select_paginated(
        sb,
        "equip_grupos",
        "id",
        tenant_id__eq=tenant_id)
    gids = [str(g["id"]) for g in grupos if g.get("id")]
    if not gids:
        return EMPTY

    # Busca equipamentos via RPC (SECURITY DEFINER) para contornar RLS scope-restritivo
    # que bloqueia SELECT geral mas permite acesso pela função com permissão de owner.
    eq_rows = []
    try:
        rpc_result = sb.rpc(
            "get_equipamentos_dashboard",
            {"p_tenant_id": tenant_id}
        ).execute()
        eq_rows = rpc_result.data or []
    except Exception:
        pass

    # Fallback para safe_select_paginated se RPC não disponível.
    # safe_select simples truncaria silenciosamente em tenants com >1000 equipamentos.
    if not eq_rows:
        eq_rows = safe_select_paginated(
            sb,
            "equipamentos",
            "id,grupo_id",
            tenant_id__eq=tenant_id,
            ativo__eq=True,
            grupo_id__in=gids)

    grp_to_eq: dict[str, list[str]] = defaultdict(list)
    eq_to_gid: dict[str, str] = {}
    for r in eq_rows:
        gid = str(r.get("grupo_id")) if r.get("grupo_id") else None
        eid = str(r.get("id")) if r.get("id") else None
        if gid and eid:
            grp_to_eq[gid].append(eid)
            eq_to_gid[eid] = gid

    # Considera apenas serviços ativos para manter consistência com a Matriz/PDF.
    active_service_rows = safe_select_paginated(
        sb,
        "servicos",
        "id",
        tenant_id__eq=tenant_id,
        ativo__eq=True,
    )
    active_service_ids = {str(r.get("id")) for r in active_service_rows if r.get("id")}

    # N grupos × M serviços/grupo pode facilmente exceder 1000 linhas.
    tpl_rows = safe_select_paginated(sb, "grupo_servicos", "grupo_id,servico_id",
                                     tenant_id__eq=tenant_id, grupo_id__in=gids)
    grp_to_services: dict[str, set[str]] = defaultdict(set)
    for r in tpl_rows:
        gid = str(r.get("grupo_id")) if r.get("grupo_id") else None
        sid = str(r.get("servico_id")) if r.get("servico_id") else None
        if gid and sid and (not active_service_ids or sid in active_service_ids):
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

    # Busca TODAS as tarefas da revisão de uma vez (mesma abordagem da Matriz).
    # Antes filtrava por equipamento_id IN all_eq_ids, o que causava undercount:
    # tarefas de equipamentos não resolvidos no eq_to_gid eram ignoradas.
    done_by_gid: dict[str, int] = defaultdict(int)
    if revisao_id:
        start = 0
        page_size = 1000
        while True:
            try:
                trows = (
                    sb.table("tarefas_servico")
                    .select("equipamento_id,servico_id,etapa_d,etapa_r,etapa_m")
                    .eq("tenant_id", tenant_id)
                    .eq("revisao_id", revisao_id)
                    .range(start, start + page_size - 1)
                    .execute().data
                ) or []
            except Exception as exc:
                log_error(
                    exc,
                    context="kpi_engine._compute_from_raw.all_tarefas",
                    table="tarefas_servico",
                )
                break
            for t in trows:
                eid = str(t.get("equipamento_id")) if t.get("equipamento_id") else None
                sid = str(t.get("servico_id")) if t.get("servico_id") else None
                gid = eq_to_gid.get(eid)
                if gid and (not active_service_ids or sid in active_service_ids):
                    done_by_gid[gid] += (
                        int(bool(t.get("etapa_d"))) +
                        int(bool(t.get("etapa_r"))) +
                        int(bool(t.get("etapa_m")))
                    )
            if len(trows) < page_size:
                break
            start += page_size

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
    # Inclui hash do token no ver para garantir que o cache seja invalidado
    # quando o token muda (de vazio para válido), evitando cache envenenado
    # por chamadas iniciais sem JWT que retornam dados vazios via RLS.
    import hashlib as _hl
    _base_ver = str(ver or "0")
    ver = f"{_base_ver}_{_hl.md5((_token or '').encode()).hexdigest()[:8]}"

    # Ajusta TTL dinamicamente consultando o status da revisão.
    # Revisões concluídas não mudam — podemos cache por muito mais tempo.
    status = _get_revisao_status(tenant_id, revisao_id, _token)
    if status in ("concluida", "encerrada", "fechada"):
        return _get_group_kpis_concluded(tenant_id, revisao_id, ver, _token)

    # Para revisões ativas: se houve invalidação manual de cache (apontamento
    # ou sincronização recente), pula mv_revisao_grupo_kpis — que depende de
    # triggers do banco e pode estar desatualizada — e recalcula do raw.
    # A view materializada só é usada no carregamento inicial (ver == "0").
    kpi_ver = st.session_state.get("_kpi_ver", 0)
    if prefer_mv and _base_ver == "0" and kpi_ver == 0:
        mv_rows = _fetch_mv(tenant_id, revisao_id, _token)
        if mv_rows:
            df = _mv_to_df(mv_rows)
            # Valida MV: se todos os grupos têm done_steps=0 mas há tarefas
            # na revisão, a MV está desatualizada — recalcula do raw.
            if not df.empty and df["done_steps"].sum() > 0:
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
