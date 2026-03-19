"""KPI helpers shared between Matriz and Home.

Goal: keep the exact same progress logic used by the Matriz group cards.

Important: The Matriz "cards" KPI counts done steps by summing
etapa_d/etapa_r/etapa_m across *all* tarefas_servico rows for the group's
active equipments within the selected revisao (it does not filter by template
service_id). We intentionally keep the same behavior here to guarantee equality.
"""

from __future__ import annotations

from collections import defaultdict

import streamlit as st

from src.utils.supabase_helpers import sb_for_user


@st.cache_data(ttl=60, show_spinner=False)
def group_kpis(tenant_id: str, revisao_id: str, _token: str = "",
               ver: str = "0") -> dict[str, dict]:
    """Return dict keyed by grupo_id with eq_count, svc_count, pct.

    Matches the logic used by the Matriz selection cards.
    """

    sb = sb_for_user()

    # grupos ativos
    grupos = (
        sb.table("equip_grupos")
        .select("id")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .execute()
        .data
    ) or []
    gids = [g.get("id") for g in grupos if g.get("id")]
    if not gids or not revisao_id:
        return {}

    # equipamentos ativos -> grupo
    eq_rows = (
        sb.table("equipamentos")
        .select("id,grupo_id")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .in_("grupo_id", gids)
        .execute()
        .data
    ) or []

    grp_to_eq: dict[str, set] = defaultdict(set)
    for e in eq_rows:
        gid = e.get("grupo_id")
        eid = e.get("id")
        if gid and eid:
            grp_to_eq[gid].add(eid)

    # serviços do template por grupo
    gs_rows = (
        sb.table("grupo_servicos")
        .select("grupo_id,servico_id")
        .eq("tenant_id", tenant_id)
        .in_("grupo_id", gids)
        .execute()
        .data
    ) or []
    grp_to_services: dict[str, set] = defaultdict(set)
    for r in gs_rows:
        gid = r.get("grupo_id")
        sid = r.get("servico_id")
        if gid and sid:
            grp_to_services[gid].add(sid)

    # tarefas da revisão (somente ids de equipamentos ativos)
    all_eq_ids = [eid for _g, eids in grp_to_eq.items() for eid in eids]
    done_steps_by_gid: dict[str, int] = defaultdict(int)

    if all_eq_ids:
        CHUNK = 500

        # eq->gid rápido (monta 1x)
        eq_to_gid: dict[str, str] = {}
        for gid, eids in grp_to_eq.items():
            for eid in eids:
                eq_to_gid[eid] = gid

        for i in range(0, len(all_eq_ids), CHUNK):
            chunk = all_eq_ids[i: i + CHUNK]
            trows = (
                sb.table("tarefas_servico")
                .select("equipamento_id,etapa_d,etapa_r,etapa_m")
                .eq("tenant_id", tenant_id)
                .eq("revisao_id", revisao_id)
                .in_("equipamento_id", chunk)
                .execute()
                .data
            ) or []

            for t in trows:
                eid = t.get("equipamento_id")
                gid = eq_to_gid.get(eid)
                if not gid:
                    continue
                done_steps_by_gid[gid] += (
                    int(bool(t.get("etapa_d")))
                    + int(bool(t.get("etapa_r")))
                    + int(bool(t.get("etapa_m")))
                )

    out: dict[str, dict] = {}
    for gid in gids:
        eqc = len(grp_to_eq.get(gid) or [])
        svc = len(grp_to_services.get(gid) or set())
        expected = max(eqc * svc * 3, 1)
        done = int(done_steps_by_gid.get(gid, 0))
        pct = int(
            round(
                (done /
                 expected) *
                100)) if (
            eqc > 0 and svc > 0) else 0
        out[gid] = {
            "eq_count": eqc,
            "svc_count": svc,
            "pct": max(0, min(100, pct)),
            "done": done,
            "expected": expected,
        }

    return out
