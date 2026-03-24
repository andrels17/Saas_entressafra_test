"""Contagens para badges da sidebar.

Estratégia de performance:
  - Antes: 5 queries separadas por rerun (ttl=30s)
  - Depois: 2 queries por rerun (ttl=60s)
    · Query 1: revisão ativa (1 row)
    · Query 2: tarefas_servico com todos os status de uma vez (1 query)
      agrupa localmente em Python → elimina 3 round-trips ao banco
  - auditoria_24h mantida como best-effort separada (tabela diferente)

TTL de 60s é adequado para badges: são indicativos, não críticos.
O usuário percebe latência de query muito mais do que um badge com
60s de defasagem.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import streamlit as st

from src.utils.supabase_helpers import current_tenant_id
from src.db.supabase_client import get_supabase_anon


def _safe_count(res) -> int:
    """Extrai count de respostas do supabase-py com tolerância a versões."""
    if res is None:
        return 0
    if hasattr(res, "count") and getattr(res, "count") is not None:
        try:
            return int(getattr(res, "count"))
        except Exception:
            return 0
    try:
        if isinstance(res, dict) and res.get("count") is not None:
            return int(res["count"])
    except Exception:
        pass
    return 0


@st.cache_data(ttl=60, show_spinner=False)
def get_sidebar_badges(tenant_id: str, _token: str = "") -> dict[str, int]:
    """Badges da sidebar em 3 queries leves (era 1 query que trazia até 5 000 linhas).

    Query 1 — revisão ativa: busca o id da revisão com status 'ativa'.
    Query 2 — contagens agregadas por status diretamente no banco:
               usa group-by no PostgREST para evitar trazer todas as linhas.
               Calcula travados, pendentes em uma única chamada.
    Query 2b — equipamentos parados: só busca timestamps se necessário,
               limitada a 1 000 linhas (parados há > 7 dias são raros).
    Query 3 — historico_eventos: contagem de auditoria 24h (best-effort).
    """
    sb = get_supabase_anon()
    if _token:
        sb.postgrest.auth(_token)

    # ── Query 1: revisão ativa ───────────────────────────────────────────────
    rev = (
        sb.table("revisoes")
        .select("id")
        .eq("tenant_id", tenant_id)
        .eq("status", "ativa")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
    ) or []

    if not rev:
        return {"gestor_travados": 0, "equip_parados": 0,
                "apont_pendentes": 0, "auditoria_24h": 0}

    revisao_id = rev[0]["id"]

    # ── Query 2: contagens por status — agregadas no banco ──────────────────
    # Busca apenas travados e pendentes usando count="exact" por status.
    # Evita trazer o payload completo de 5 000 linhas para Python.
    travados = pendentes = parados = 0
    try:
        r_trav = (
            sb.table("tarefas_servico")
            .select("id", count="exact")
            .eq("tenant_id", tenant_id)
            .eq("revisao_id", revisao_id)
            .eq("status", "travado")
            .execute()
        )
        travados = _safe_count(r_trav)

        r_pend = (
            sb.table("tarefas_servico")
            .select("id", count="exact")
            .eq("tenant_id", tenant_id)
            .eq("revisao_id", revisao_id)
            .in_("status", ["pendente", "em_andamento"])
            .execute()
        )
        pendentes = _safe_count(r_pend)
    except Exception:
        travados = pendentes = 0

    # ── Query 2b: equipamentos parados (≥ 7 dias sem movimentação) ──────────
    # Limitado a 1 000 linhas: parados de longa data são minoria.
    # Traz apenas equipamento_id + timestamps das etapas, sem status nem outros campos.
    try:
        from src.utils.timezone import days_since_utc

        rows_mov = (
            sb.table("tarefas_servico")
            .select("equipamento_id,dt_etapa_d,dt_etapa_r,dt_etapa_m")
            .eq("tenant_id", tenant_id)
            .eq("revisao_id", revisao_id)
            .not_.in_("status", ["concluido", "nao_aplica"])
            .limit(1000)
            .execute()
            .data
        ) or []

        ultimos: dict[str, str] = {}
        for row in rows_mov:
            eid = row.get("equipamento_id")
            if not eid:
                continue
            mov = max(
                [x for x in [
                    row.get("dt_etapa_m"),
                    row.get("dt_etapa_r"),
                    row.get("dt_etapa_d"),
                ] if x],
                default=None,
            )
            if mov and (eid not in ultimos or mov > ultimos[eid]):
                ultimos[eid] = mov

        parados = sum(
            1 for mov in ultimos.values()
            if (days_since_utc(mov) or 0) >= 7
        )
    except Exception:
        parados = 0

    # ── Query 3: auditoria 24h (best-effort, tabela separada) ───────────────
    auditoria = 0
    try:
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        r3 = (
            sb.table("historico_eventos")
            .select("id", count="exact")
            .eq("tenant_id", tenant_id)
            .gte("created_at", since)
            .execute()
        )
        auditoria = _safe_count(r3)
    except Exception:
        auditoria = 0

    return {
        "gestor_travados": int(travados),
        "equip_parados":   int(parados),
        "apont_pendentes": int(pendentes),
        "auditoria_24h":   int(auditoria),
    }


def sidebar_badges() -> dict[str, int]:
    """Helper que usa o tenant atual do session_state."""
    try:
        token = st.session_state.get("sb_access_token", "")
        return get_sidebar_badges(current_tenant_id(), token)
    except Exception:
        return {"gestor_travados": 0, "equip_parados": 0,
                "apont_pendentes": 0, "auditoria_24h": 0}
