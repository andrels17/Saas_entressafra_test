"""Painel de geração do Relatório Executivo PDF."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

import streamlit as st

from src.ui.admin.branding_tabs.pdf_utils import (
    Branding,
    build_executive_pdf,
    resp_data,
)


def _load_revisoes(sb, tenant_id: str) -> list:
    try:
        return (
            sb.table("revisoes")
            .select("id,titulo,data_inicio,data_fim,semanas_total,status")
            .eq("tenant_id", tenant_id)
            .order("data_inicio", desc=True)
            .execute()
            .data
        ) or []
    except Exception:
        return []


def _load_grupos(sb, tenant_id: str) -> list:
    try:
        rows = (
            sb.table("equip_grupos").select("id,nome,ativo")
            .eq("tenant_id", tenant_id).execute().data
        ) or []
        rows = [g for g in rows if g.get("ativo", True)]
        return sorted(rows, key=lambda x: x.get("nome") or "")
    except Exception:
        try:
            return (
                sb.table("equip_grupos").select("id,nome")
                .eq("tenant_id", tenant_id).order("nome").execute().data
            ) or []
        except Exception:
            return []


def _load_equipamentos(sb, tenant_id: str) -> tuple[list, str]:
    label_field = "nome"
    try:
        rows = (
            sb.table("equipamentos").select("id,nome")
            .eq("tenant_id", tenant_id).order("nome").execute().data
        ) or []
        return rows, label_field
    except Exception:
        try:
            label_field = "codigo"
            rows = (
                sb.table("equipamentos").select("id,codigo")
                .eq("tenant_id", tenant_id).order("codigo").execute().data
            ) or []
            return rows, label_field
        except Exception:
            return [], label_field


def _call_rpc_with_fallback(sb, tenant_id: str, dt_ini: date, dt_fim: date,
                            grupos_ids, status_vals, equipamentos_ids,
                            agg_semana: bool, revisao_id: Optional[str]):
    """Tenta get_executive_summary v3 → v2 → básico, retornando (data, aviso?)."""
    base = {
        "p_tenant_id": tenant_id,
        "p_dt_ini": datetime.combine(dt_ini, datetime.min.time()).isoformat(),
        "p_dt_fim": datetime.combine(dt_fim, datetime.max.time()).isoformat(),
        "p_tabela": None,
        "p_acao": None,
    }
    v3 = {**base, "p_grupos": grupos_ids, "p_status": status_vals,
          "p_agg_semana": bool(agg_semana), "p_revisao_id": revisao_id,
          "p_equipamentos": equipamentos_ids}
    v2 = {**base, "p_grupos": grupos_ids, "p_status": status_vals,
          "p_agg_semana": bool(agg_semana)}

    for payload, warn in [(v3, None), (v2, "⚠️ Backend ainda não aceita revisão/equipamentos. Filtros básicos aplicados."),
                          (base, "⚠️ Backend não aceita filtros avançados. PDF com filtros básicos.")]:
        try:
            res = sb.rpc("get_executive_summary", payload).execute()
            if warn:
                st.caption(warn)
            return resp_data(res)
        except Exception:
            continue
    return None


def render_tab_relatorio(sb, tenant_id: str, branding: Branding):
    """Renderiza o painel de geração do PDF executivo."""
    st.subheader("📄 Relatório Executivo PDF")

    today = date.today()
    dt_ini = st.date_input(
        "Data inicial",
        value=today -
        timedelta(
            days=7),
        key="rep_dt_ini")
    dt_fim = st.date_input("Data final", value=today, key="rep_dt_fim")

    revisoes = _load_revisoes(sb, tenant_id)
    grupos = _load_grupos(sb, tenant_id)
    equipamentos, equip_label_field = _load_equipamentos(sb, tenant_id)

    # ── Filtro: Revisão ─────────────────────────────────────────────────────
    rev_options: dict[str, Any] = {}
    for r in revisoes:
        titulo = r.get("titulo") or "Revisão"
        ini_s = str(r.get("data_inicio", ""))[:10]
        fim_s = str(r.get("data_fim", ""))[:10]
        label = f"{titulo} • {ini_s}–{fim_s}"
        if r.get("semanas_total") is not None:
            label += f" • {r['semanas_total']} semanas"
        if r.get("status"):
            label += f" • {r['status']}"
        rev_options[label] = r.get("id")

    rev_label = st.selectbox(
        "Revisão (recomendado para evolução por semana operacional)",
        ["(todas)"] +
        list(
            rev_options.keys()))
    revisao_id = None if rev_label == "(todas)" else rev_options.get(rev_label)

    # ── Filtros secundários ─────────────────────────────────────────────────
    st.markdown("**Filtros**")
    cA, cB = st.columns(2)

    with cA:
        grupo_map = {g.get("nome") or g.get("id"): g.get("id") for g in grupos}
        grupos_sel = st.multiselect(
            "Grupos", list(
                grupo_map.keys()), default=[])
        grupos_ids = [grupo_map[x] for x in grupos_sel] or None

        status_sel = st.multiselect(
            "Status", [
                "pendente", "em_andamento", "concluido", "travado", "nao_aplica"], default=[])
        status_vals = status_sel or None

    with cB:
        equip_map = {e.get(equip_label_field) or e.get(
            "id"): e.get("id") for e in equipamentos}
        equip_sel = st.multiselect(
            "Equipamentos", list(
                equip_map.keys()), default=[])
        equip_ids = [equip_map[x] for x in equip_sel] or None

        agg_semana = st.toggle(
            "Mostrar evolução por semana (operacional)",
            value=True,
            help="Usa tarefas_servico.semana.")
        show_percent = st.toggle("Mostrar % concluído no PDF", value=True)

    # ── Validação ───────────────────────────────────────────────────────────
    can_generate = True
    if agg_semana and not revisao_id:
        st.warning(
            "Para evolução semanal operacional, selecione uma revisão. "
            "Sem revisão, semanas de revisões diferentes podem se misturar.")
        can_generate = False

    # ── Geração ─────────────────────────────────────────────────────────────
    if st.button(
        "Gerar PDF",
        icon=":material/picture_as_pdf:",
        type="primary",
        use_container_width=True,
            disabled=not can_generate):
        with st.spinner("Gerando resumo executivo..."):
            raw = _call_rpc_with_fallback(sb, tenant_id, dt_ini, dt_fim,
                                          grupos_ids, status_vals, equip_ids,
                                          agg_semana, revisao_id)
            if raw is None:
                st.error("Erro ao chamar RPC get_executive_summary.")
                return
            summary = raw[0] if isinstance(raw, list) and raw else raw
            if not summary:
                st.warning("RPC retornou vazio — sem dados para este recorte.")
                return

        with st.spinner("Montando PDF..."):
            period_label = f"{
                dt_ini.strftime('%d/%m/%Y')} – {
                dt_fim.strftime('%d/%m/%Y')}"
            tenant_label = f"Tenant: {tenant_id[:8]}…"
            pdf_bytes = build_executive_pdf(summary, branding,
                                            period_label=period_label,
                                            tenant_label=tenant_label,
                                            show_percent=bool(show_percent))

        if pdf_bytes is None:
            st.error("PDF não foi gerado (pdf_bytes=None).")
            return
        if hasattr(pdf_bytes, "getvalue"):
            pdf_bytes = pdf_bytes.getvalue()
        if not isinstance(pdf_bytes, (bytes, bytearray)):
            st.error(f"PDF inválido: tipo retornado = {type(pdf_bytes)}")
            return

        st.download_button(
            "⬇️ Baixar Relatório Executivo (PDF)",
            data=pdf_bytes,
            file_name=f"relatorio_executivo_{tenant_id[:6]}_{dt_ini}_{dt_fim}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
