from __future__ import annotations

import streamlit as st
from datetime import datetime, timedelta, timezone
from src.utils.supabase_helpers import sb_for_user, current_tenant_id


def _safe_count(res) -> int:
    """Extrai count de respostas do supabase-py com tolerância a versões."""
    if res is None:
        return 0
    # supabase-py costuma retornar objeto com atributo .count
    if hasattr(res, "count") and getattr(res, "count") is not None:
        try:
            return int(getattr(res, "count"))
        except Exception:
            return 0
    # fallback (algumas versões retornam dict)
    try:
        if isinstance(res, dict) and res.get("count") is not None:
            return int(res["count"])
    except Exception:
        pass
    return 0


@st.cache_data(ttl=30, show_spinner=False)
def get_sidebar_badges(tenant_id: str) -> dict[str, int]:
    """Contagens rápidas para badges na sidebar.

    Mantém leve: 2 contagens principais + 1 opcional (auditoria 24h).
    """
    sb = sb_for_user()

    # revisão ativa (se não existir, badges ficam 0)
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
        return {
            "gestor_travados": 0,
            "equip_parados": 0,
            "apont_pendentes": 0,
            "auditoria_24h": 0}
    revisao_id = rev[0]["id"]

    # travados (Painel do Gestor)
    r1 = (
        sb.table("tarefas_servico")
        .select("id", count="exact")
        .eq("tenant_id", tenant_id)
        .eq("revisao_id", revisao_id)
        .eq("status", "travado")
        .execute()
    )
    travados = _safe_count(r1)

    # equipamentos parados (sem atualização recente)
    try:
        rows = (
            sb.table("tarefas_servico")
            .select("equipamento_id,status,updated_at,dt_etapa_d,dt_etapa_r,dt_etapa_m")
            .eq("tenant_id", tenant_id)
            .eq("revisao_id", revisao_id)
            .limit(5000)
            .execute()
            .data
        ) or []
        from src.utils.timezone import days_since_utc
        ultimos: dict[str, tuple[str | None, str]] = {}
        for row in rows:
            eid = row.get("equipamento_id")
            if not eid:
                continue
            mov = max([x for x in [row.get("dt_etapa_m"), row.get("dt_etapa_r"), row.get(
                "dt_etapa_d"), row.get("updated_at")] if x] or [None])
            prev = ultimos.get(eid)
            if prev is None or ((mov or "") > (prev[0] or "")):
                ultimos[eid] = (mov, row.get("status") or "pendente")
        parados = sum(
            1 for mov,
            status in ultimos.values() if status != "concluido" and (
                days_since_utc(mov) or 0) >= 7)
    except Exception:
        parados = 0

    # pendentes (Apontamento) = pendente + em_andamento
    r2 = (
        sb.table("tarefas_servico")
        .select("id", count="exact")
        .eq("tenant_id", tenant_id)
        .eq("revisao_id", revisao_id)
        .in_("status", ["pendente", "em_andamento"])  # type: ignore
        .execute()
    )
    pendentes = _safe_count(r2)

    # auditoria (últimas 24h) — best-effort
    try:
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        r3 = (
            sb.table("historico_eventos")
            .select("id", count="exact")
            .eq("tenant_id", tenant_id)
            .gte("created_at", since)
            .execute()
        )
        audit_24h = _safe_count(r3)
    except Exception:
        audit_24h = 0

    return {
        "gestor_travados": int(travados),
        "equip_parados": int(parados),
        "apont_pendentes": int(pendentes),
        "auditoria_24h": int(audit_24h),
    }


def sidebar_badges() -> dict[str, int]:
    """Helper que usa o tenant atual do session_state."""
    try:
        return get_sidebar_badges(current_tenant_id())
    except Exception:
        return {
            "gestor_travados": 0,
            "equip_parados": 0,
            "apont_pendentes": 0,
            "auditoria_24h": 0}
