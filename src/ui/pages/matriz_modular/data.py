from __future__ import annotations

from collections import defaultdict

import streamlit as st

from src.utils.supabase_helpers import sb_for_user

def _group_kpis(_tid, _rev_id, _ver="0"):
    _sb = sb_for_user()
    _gids = [
        g.get("id") for g in (
            _sb.table("equip_grupos").select("id").eq(
                "tenant_id",
                _tid).eq(
                "ativo",
                True).execute().data or []) if g.get("id")]
    if not _gids:
        return {}
    eq_rows = (
        _sb.table("equipamentos").select("id,grupo_id").eq(
            "tenant_id",
            _tid).eq(
            "ativo",
            True).in_(
                "grupo_id",
            _gids).execute().data) or []
    grp_eq = defaultdict(list)
    for r in eq_rows:
        if r.get("grupo_id") and r.get("id"):
            grp_eq[r["grupo_id"]].append(r["id"])
    tpl_rows = (
        _sb.table("grupo_servicos").select("grupo_id,servico_id").eq(
            "tenant_id",
            _tid).in_(
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
                   .eq("tenant_id", _tid).eq("revisao_id", _rev_id).in_("equipamento_id", all_eq[i:i + 500])
                   .execute().data) or []):
            gid = eq2g.get(t.get("equipamento_id"))
            if gid:
                done[gid] += int(bool(t.get("etapa_d"))) + \
                    int(bool(t.get("etapa_r"))) + int(bool(t.get("etapa_m")))
    out = {}
    for gid in _gids:
        eqc = len(grp_eq.get(gid) or [])
        svc = len(grp_svc.get(gid) or set())
        pct = int(round((done.get(gid, 0) / max(eqc * svc * 3, 1))
                  * 100)) if (eqc > 0 and svc > 0) else 0
        out[gid] = {
            "eq_count": eqc, "svc_count": svc, "pct": max(
                0, min(
                    100, pct))}
    return out


@st.cache_data(ttl=60, show_spinner=False)
def _load_payload(_tid, _gid, _rid, _lim, _ver="0", _token=""):
    _sb = sb_for_user()
    _eqs = (
        _sb.table("equipamentos").select("id,frota,modelo").eq(
            "tenant_id",
            _tid) .eq(
            "grupo_id",
            _gid).eq(
                "ativo",
                True).order("frota").limit(
                    int(_lim)).execute().data) or []
    if not _eqs:
        return {"eqs": [], "s2s": {}, "all_s": [], "tarefas": []}
    _s2s, _all_s = _fetch_template(_sb, _tid, _gid)
    if not _all_s:
        return {"eqs": _eqs, "s2s": {}, "all_s": [], "tarefas": []}
    _tarefas = (
        _sb.table("tarefas_servico") .select(
            "id,equipamento_id,servico_id,status,semana,observacao,"
            "etapa_d,etapa_r,etapa_m,dt_inicio,dt_etapa_d,dt_etapa_r,dt_etapa_m") .eq(
            "tenant_id", _tid).eq(
                "revisao_id", _rid) .in_(
                    "equipamento_id", [
                        e["id"] for e in _eqs]).execute().data) or []
    return {"eqs": _eqs, "s2s": _s2s, "all_s": _all_s, "tarefas": _tarefas}


def _fetch_template(sb, tenant_id, grupo_id):
    for select, setor_fn in [
        ("servico_id, servicos(id,nome,setor_id,setores(nome))",
         lambda sv: (sv.get("setores") or {}).get("nome") or "Setor"),
        ("servico_id, servicos(id,nome,setor)",
         lambda sv: sv.get("setor") or "Setor"),
    ]:
        try:
            tpl = (
                sb.table("grupo_servicos").select(select) .eq(
                    "tenant_id",
                    tenant_id).eq(
                    "grupo_id",
                    grupo_id).execute().data) or []
            s2s = defaultdict(list)
            all_s = []
            for r in tpl:
                sv = r.get("servicos") or {}
                sid = sv.get("id")
                if not sid:
                    continue
                s2s[setor_fn(sv)].append(sv)
                all_s.append(sv)
            if all_s:
                return s2s, all_s
        except Exception:
            pass
    tpl = (
        sb.table("grupo_servicos").select("servico_id") .eq(
            "tenant_id",
            tenant_id).eq(
            "grupo_id",
            grupo_id).execute().data) or []
    ids = [r.get("servico_id") for r in tpl if r.get("servico_id")]
    if not ids:
        return defaultdict(list), []
    svs = (sb.table("servicos").select("id,nome,setor")
           .eq("tenant_id", tenant_id).in_("id", ids).execute().data) or []
    s2s = defaultdict(list)
    all_s = []
    for sv in svs:
        sn = sv.get("setor") or "Setor"
        item = {"id": sv.get("id"), "nome": sv.get("nome")}
        s2s[sn].append(item)
        all_s.append(item)
    return s2s, all_s


@st.cache_data(ttl=300, show_spinner=False)
def _dept_name(_tid, _did, _ver="0", _token=""):
    if not _did:
        return ""
    try:
        row = (
            sb_for_user().table("departamentos").select("nome").eq(
                "tenant_id", _tid).eq(
                "id", _did).limit(1).execute().data)
        return (row[0].get("nome") or "") if row else ""
    except BaseException:
        return ""


@st.cache_data(ttl=300, show_spinner=False)
def _all_dept_names(_tid, _ver="0", _token=""):
    try:
        rows = sb_for_user().table("departamentos").select(
            "id,nome").eq("tenant_id", _tid).execute().data or []
        return {r["id"]: r.get("nome", "") for r in rows}
    except BaseException:
        return {}


