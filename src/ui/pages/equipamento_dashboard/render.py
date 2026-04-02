"""Dashboard por Equipamento — visão completa de um equipamento específico.

Acessado via busca global na sidebar (ícone de lupa).
Exibe:
  - Cabeçalho: frota, modelo, grupo, departamento, status ativo/inativo
  - KPIs da revisão ativa: % concluído, etapas D/R/M, travados, pendentes
  - Tabela de tarefas com status, setor, serviço, etapas e observação
  - Histórico de % concluído nas últimas revisões encerradas
"""
from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

from src.ui.core.styles import page_header, status_badge
from src.ui.core.plotly_theme import apply_dark_theme
from src.ui.core.empty_state import empty_state
from src.ui.components.tables import data_table
from src.utils.supabase_helpers import current_tenant_id
from src.utils.nav import get_current_revisao

from .data import (
    load_equipamento_detail,
    load_tarefas_equipamento,
    load_revisao_ativa,
    load_historico_revisoes,
)

_STATUS_LABEL = {
    "concluido":    "Concluído",
    "concluído":    "Concluído",
    "em_andamento": "Em andamento",
    "pendente":     "Pendente",
    "travado":      "Travado",
    "nao_aplica":   "N/A",
}

_STATUS_COLOR = {
    "concluido":    "#38A169",
    "concluído":    "#38A169",
    "em_andamento": "#3182CE",
    "pendente":     "#D69E2E",
    "travado":      "#C53030",
    "nao_aplica":   "#718096",
}


def _pct_bar_color(pct: float) -> str:
    if pct >= 80:
        return "#38A169"
    if pct >= 50:
        return "#D69E2E"
    return "#C53030"


def _bool_icon(val) -> str:
    return "✅" if val else "⬜"


def _build_tarefas_df(tarefas: list[dict]) -> pd.DataFrame:
    rows = []
    for t in tarefas:
        svc = t.get("servicos") or {}
        setor = (svc.get("setores") or {}).get("nome") or "—"
        status_raw = t.get("status") or "pendente"
        rows.append({
            "Serviço": svc.get("nome") or "—",
            "Setor": setor,
            "Status": _STATUS_LABEL.get(status_raw, status_raw),
            "D": _bool_icon(t.get("etapa_d")),
            "R": _bool_icon(t.get("etapa_r")),
            "M": _bool_icon(t.get("etapa_m")),
            "Semana": t.get("semana") or "—",
            "Observação": t.get("observacao") or "",
            "_status_raw": status_raw,
        })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    # Ordena: travado > pendente > em_andamento > concluido > nao_aplica
    order = {"travado": 0, "pendente": 1, "em_andamento": 2, "concluido": 3, "nao_aplica": 4}
    df["_order"] = df["_status_raw"].map(lambda s: order.get(s, 9))
    df = df.sort_values("_order").drop(columns=["_order", "_status_raw"])
    return df


def _render_kpis(tarefas: list[dict]) -> dict:
    """Calcula e renderiza KPIs. Retorna dict com totais."""
    total_steps = len(tarefas) * 3
    done_steps = sum(
        int(bool(t.get("etapa_d"))) + int(bool(t.get("etapa_r"))) + int(bool(t.get("etapa_m")))
        for t in tarefas
    )
    pct = round((done_steps / max(total_steps, 1)) * 100)

    by_status: dict[str, int] = {}
    for t in tarefas:
        s = t.get("status") or "pendente"
        by_status[s] = by_status.get(s, 0) + 1

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("% Concluído", f"{pct}%",
              help="Percentual de etapas (D+R+M) concluídas sobre o total esperado.")
    c2.metric("Tarefas", len(tarefas))
    c3.metric("⛔ Travadas", by_status.get("travado", 0),
              delta_color="inverse" if by_status.get("travado") else "off")
    c4.metric("⏳ Pendentes", by_status.get("pendente", 0))
    c5.metric("✅ Concluídas", by_status.get("concluido", 0))

    return {"pct": pct, "total": len(tarefas), "by_status": by_status}


def _render_historico_chart(historico: list[dict]) -> None:
    """Gráfico de barras com % concluído nas últimas revisões."""
    if not historico:
        st.caption("Sem revisões encerradas para exibir histórico.")
        return

    import plotly.express as px
    df = pd.DataFrame(historico)
    df = df[df["total"] > 0]
    if df.empty:
        st.caption("Nenhum dado de tarefas em revisões anteriores.")
        return

    df["color"] = df["pct"].apply(_pct_bar_color)
    df["label"] = df["pct"].map(lambda v: f"{v}%")

    fig = px.bar(
        df,
        x="titulo",
        y="pct",
        text="label",
        color="color",
        color_discrete_map="identity",
        title="Histórico por revisão",
    )
    fig.update_traces(
        textposition="outside",
        cliponaxis=False,
        textfont=dict(color="#F5F5F5"),
    )
    apply_dark_theme(fig, height=260)
    fig.update_layout(
        margin=dict(l=6, r=6, t=36, b=10),
        xaxis=dict(title="", tickfont=dict(color="#8A9BAE")),
        yaxis=dict(title="% Concluído", range=[0, 115],
                   tickfont=dict(color="#8A9BAE"),
                   title_font=dict(color="#8A9BAE")),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def render_equipamento_dashboard() -> None:
    """Entry point da página de Dashboard por Equipamento."""
    equipamento_id = st.session_state.get("_equip_detail_id")

    if not equipamento_id:
        empty_state(
            icon="◫",
            title="Nenhum equipamento selecionado",
            description="Use a busca (🔍) na sidebar para encontrar e abrir um equipamento.",
            action_label="Ir para o Início",
            action_key="equip_dash_goto_home",
            nav_to="Início",
        )
        return

    tenant_id = current_tenant_id()
    token = st.session_state.get("sb_access_token", "")
    token_key = hashlib.md5(token.encode()).hexdigest()[:8]

    # ── Carrega dados ────────────────────────────────────────────────────────
    equip = load_equipamento_detail(tenant_id, equipamento_id, token_key, token)
    if not equip:
        st.error("Equipamento não encontrado ou sem acesso.")
        if st.button("← Voltar", key="equip_dash_back"):
            st.session_state.pop("_equip_detail_id", None)
            st.session_state["__nav_to"] = "Início"
            st.rerun()
        return

    revisao_id = get_current_revisao()
    if not revisao_id:
        rev = load_revisao_ativa(tenant_id, token_key, token)
        if rev:
            revisao_id = str(rev["id"])
    else:
        rev = None  # já temos o ID, não precisamos buscar o objeto completo

    # ── Cabeçalho ────────────────────────────────────────────────────────────
    page_header("◫", f"{equip['frota']} — {equip['modelo']}", equip["grupo_nome"])

    col_info, col_back = st.columns([5, 1])
    with col_info:
        st.markdown(
            f'<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:4px">'
            f'<span style="font-size:.82rem;color:#8A9BAE">🏭 Departamento: '
            f'<b style="color:#E8EDF5">{equip["departamento_nome"]}</b></span>'
            f'<span style="font-size:.82rem;color:#8A9BAE">⊕ Grupo: '
            f'<b style="color:#E8EDF5">{equip["grupo_nome"]}</b></span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        status_badge("ativa" if equip["ativo"] else "fechada")
    with col_back:
        if st.button("← Voltar", key="equip_dash_back_top", type="tertiary"):
            st.session_state.pop("_equip_detail_id", None)
            st.rerun()

    st.divider()

    if not revisao_id:
        st.warning("Nenhuma revisão ativa encontrada para este tenant.")
        return

    tarefas = load_tarefas_equipamento(tenant_id, equipamento_id, revisao_id, token_key, token)

    # ── KPIs ─────────────────────────────────────────────────────────────────
    st.markdown("### Revisão atual")
    if not tarefas:
        st.info("Este equipamento não possui tarefas na revisão ativa.")
    else:
        _render_kpis(tarefas)
        st.divider()

        # ── Tabela de tarefas ────────────────────────────────────────────────
        st.markdown("### Tarefas")
        df = _build_tarefas_df(tarefas)
        if not df.empty:
            data_table(
                df,
                column_config={
                    "D": st.column_config.TextColumn("D", width="small",
                        help="Etapa Diagnóstico"),
                    "R": st.column_config.TextColumn("R", width="small",
                        help="Etapa Reparo"),
                    "M": st.column_config.TextColumn("M", width="small",
                        help="Etapa Manutenção"),
                    "Semana": st.column_config.NumberColumn("Semana", width="small"),
                    "Observação": st.column_config.TextColumn("Observação", width="large"),
                },
            )

    # ── Histórico ────────────────────────────────────────────────────────────
    st.divider()
    st.markdown("### Histórico de revisões")
    historico = load_historico_revisoes(tenant_id, equipamento_id, token_key, token)
    _render_historico_chart(historico)
