from __future__ import annotations

import streamlit as st

from src.repositories.base import fetch_grupo_template as _fetch_template_impl
from src.db.supabase_client import get_supabase_anon, get_supabase_service


def _sb_from_token(token: str = ""):
    """Cliente de leitura para a Matriz.

    Usa service-role quando disponível para evitar bases zeradas em perfis com
    RLS mais restritivo. O acesso ao grupo já foi filtrado antes pela camada de
    escopo da aplicação.
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


def _group_kpis(tid, rev_id, ver="0", _token=""):
    """Delega para kpi_engine.get_group_kpis — fonte única de verdade para KPIs.

    Elimina a duplicação de 3–5 queries que existia aqui independentemente
    de kpi_engine. kpi_engine já possui TTL adaptativo (60s ativo / 1h
    concluído), fallback para materialized view e suporte a invalidation
    via invalidate_kpi_cache().
    """
    from src.utils.kpi_engine import get_group_kpis
    df = get_group_kpis(tid, rev_id, ver=ver, _token=_token)
    if df.empty:
        return {}
    out = {}
    for _, row in df.iterrows():
        gid = row.get("grupo_id")
        if gid:
            out[gid] = {
                "eq_count": int(row.get("eq_count", 0)),
                "svc_count": int(row.get("svc_count", 0)),
                "pct": int(row.get("pct", 0)),
            }
    return out


@st.cache_data(ttl=60, show_spinner=False)
def _load_payload(tid, gid, rid, lim, ver="0", _token=""):
    """Carrega equipamentos, template e tarefas de um grupo/revisão.

    Chave de cache: (tid, gid, rid, lim, ver) — `rid` incluído explicitamente
    para evitar que uma mudança de revisão sem alteração de `lim` retorne o
    payload da revisão anterior durante o TTL.
    """
    _sb = _sb_from_token(_token)
    _eqs = (
        _sb.table("equipamentos").select("id,frota,modelo").eq(
            "tenant_id",
            tid) .eq(
            "grupo_id",
            gid).eq(
                "ativo",
                True).order("frota").limit(
                    int(lim)).execute().data) or []
    if not _eqs:
        return {"eqs": [], "s2s": {}, "all_s": [], "tarefas": []}

    # Exclui equipamentos ocultos nesta revisão
    try:
        from src.utils.eq_oculto import get_ocultos
        _ocultos = get_ocultos(_sb, tid, rid)
        if _ocultos:
            _eqs = [e for e in _eqs if e.get("id") not in _ocultos]
        if not _eqs:
            return {"eqs": [], "s2s": {}, "all_s": [], "tarefas": []}
    except Exception:
        pass
    _s2s, _all_s = _fetch_template(_sb, tid, gid)
    if not _all_s:
        return {"eqs": _eqs, "s2s": {}, "all_s": [], "tarefas": []}
    _tarefas = (
        _sb.table("tarefas_servico") .select(
            "id,equipamento_id,servico_id,status,semana,observacao,"
            "etapa_d,etapa_r,etapa_m,dt_inicio,dt_etapa_d,dt_etapa_r,dt_etapa_m") .eq(
            "tenant_id", tid).eq(
                "revisao_id", rid) .in_(
                    "equipamento_id", [
                        e["id"] for e in _eqs]).execute().data) or []
    return {"eqs": _eqs, "s2s": _s2s, "all_s": _all_s, "tarefas": _tarefas}


def _fetch_template(sb, tenant_id, grupo_id):
    """Delega para o repositório central — evita triplicação de lógica."""
    return _fetch_template_impl(sb, tenant_id, grupo_id)
@st.cache_data(ttl=300, show_spinner=False)
def _dept_name(tid, did, ver="0", _token=""):
    if not did:
        return ""
    try:
        row = (
            _sb_from_token(_token).table("departamentos").select("nome").eq(
                "tenant_id", tid).eq(
                "id", did).limit(1).execute().data)
        return (row[0].get("nome") or "") if row else ""
    except BaseException:
        return ""


@st.cache_data(ttl=300, show_spinner=False)
def _all_dept_names(tid, ver="0", _token=""):
    try:
        rows = _sb_from_token(_token).table("departamentos").select(
            "id,nome").eq("tenant_id", tid).execute().data or []
        return {r["id"]: r.get("nome", "") for r in rows}
    except BaseException:
        return {}
