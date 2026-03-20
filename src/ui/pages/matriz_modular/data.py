from __future__ import annotations

from collections import defaultdict

import streamlit as st

from src.utils.supabase_helpers import sb_for_user
from src.repositories.base import fetch_grupo_template as _fetch_template_impl
from src.db.supabase_client import get_supabase_anon


def _sb_from_token(token: str = ""):
    """Constrói cliente sem acessar st.session_state (seguro dentro de cache_data)."""
    sb = get_supabase_anon()
    if token:
        sb.postgrest.auth(token)
    return sb

@st.cache_data(ttl=60, show_spinner=False)
def _group_kpis(tid, rev_id, ver="0", _token=""):
    _sb = _sb_from_token(_token)
    _gids = [
        g.get("id") for g in (
            _sb.table("equip_grupos").select("id").eq(
                "tenant_id",
                tid).eq(
                "ativo",
                True).execute().data or []) if g.get("id")]
    if not _gids:
        return {}
    eq_rows = (
        _sb.table("equipamentos").select("id,grupo_id").eq(
            "tenant_id",
            tid).eq(
            "ativo",
            True).in_(
                "grupo_id",
            _gids).execute().data) or []

    # Exclui equipamentos ocultos nesta revisão
    try:
        from src.utils.eq_oculto import get_ocultos
        _ocultos = get_ocultos(_sb, tid, rev_id)
        if _ocultos:
            eq_rows = [r for r in eq_rows if r.get("id") not in _ocultos]
    except Exception:
        pass
    grp_eq = defaultdict(list)
    for r in eq_rows:
        if r.get("grupo_id") and r.get("id"):
            grp_eq[r["grupo_id"]].append(r["id"])
    tpl_rows = (
        _sb.table("grupo_servicos").select("grupo_id,servico_id").eq(
            "tenant_id",
            tid).in_(
            "grupo_id",
            _gids).execute().data) or []
    grp_svc = defaultdict(set)
    for r in tpl_rows:
        if r.get("grupo_id") and r.get("servico_id"):
            grp_svc[r["grupo_id"]].add(r["servico_id"])
    all_eq = [eid for eids in grp_eq.values() for eid in eids]
    done = defaultdict(int)
    eq2g = {eid: gid for gid, eids in grp_eq.items() for eid in eids}
    for i in range(0, len(all_eq), 500):
        for t in ((_sb.table("tarefas_servico").select("equipamento_id,etapa_d,etapa_r,etapa_m")
                   .eq("tenant_id", tid).eq("revisao_id", rev_id).in_("equipamento_id", all_eq[i:i + 500])
                   .execute().data) or []):
            gid = eq2g.get(t.get("equipamento_id"))
            if gid:
                done[gid] += int(bool(t.get("etapa_d"))) + \
                    int(bool(t.get("etapa_r"))) + int(bool(t.get("etapa_m")))
    out = {}
    for gid in _gids:
        eqc = len(grp_eq.get(gid) or [])
        svc = len(grp_svc.get(gid) or set())
        expected = eqc * svc * 3
        pct = int(round((done.get(gid, 0) / expected) * 100)) if (eqc > 0 and svc > 0 and expected > 0) else 0
        out[gid] = {
            "eq_count": eqc, "svc_count": svc, "pct": max(
                0, min(
                    100, pct))}
    return out


@st.cache_data(ttl=60, show_spinner=False)
def _load_payload(tid, gid, rid, lim, ver="0", _token=""):
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


