"""Dashboard por Equipamento — visão completa de um equipamento específico."""
from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

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
    "concluido": "Concluído", "concluído": "Concluído",
    "em_andamento": "Em andamento", "pendente": "Pendente",
    "travado": "Travado", "nao_aplica": "N/A",
}
_STATUS_COLOR = {
    "concluido": "#38A169", "concluído": "#38A169",
    "em_andamento": "#3182CE", "pendente": "#D69E2E",
    "travado": "#C53030", "nao_aplica": "#718096",
}


def _pct_bar_color(pct: float) -> str:
    if pct >= 80: return "#38A169"
    if pct >= 50: return "#D69E2E"
    return "#C53030"


def _render_header(equip: dict, pct: int) -> None:
    ativo = equip.get("ativo", True)
    status_color = "#38A169" if ativo else "#718096"
    status_label = "Ativo" if ativo else "Inativo"
    bar_color = _pct_bar_color(pct)
    st.markdown(f"""
    <div style="background:linear-gradient(135deg,rgba(255,255,255,0.04),rgba(255,255,255,0.02));
                border:1px solid rgba(255,255,255,0.08);border-radius:14px;
                padding:20px 24px 18px;margin-bottom:20px">
        <div style="display:flex;align-items:flex-start;justify-content:space-between;
                    gap:12px;margin-bottom:12px">
            <div style="min-width:0">
                <div style="font-size:0.68rem;font-weight:600;color:#8A9BAE;
                            letter-spacing:0.08em;text-transform:uppercase;margin-bottom:4px">
                    Equipamento
                </div>
                <div style="font-size:1.45rem;font-weight:800;color:#F5F5F5;
                            line-height:1.2;word-break:break-word">
                    {equip['frota']}
                    <span style="color:#8A9BAE;font-weight:400">—</span>
                    {equip['modelo']}
                </div>
            </div>
            <span style="flex-shrink:0;display:inline-block;padding:4px 12px;
                         border-radius:999px;font-size:0.72rem;font-weight:700;margin-top:4px;
                         background:{status_color}22;color:{status_color};
                         border:1px solid {status_color}55">{status_label}</span>
        </div>
        <div style="display:flex;gap:20px;flex-wrap:wrap;margin-bottom:16px">
            <span style="font-size:0.8rem;color:#8A9BAE">
                🏭 <b style="color:#C8D0DB">{equip['departamento_nome']}</b>
            </span>
            <span style="font-size:0.8rem;color:#8A9BAE">
                ⊕ <b style="color:#C8D0DB">{equip['grupo_nome']}</b>
            </span>
        </div>
        <div>
            <div style="display:flex;justify-content:space-between;
                        font-size:0.72rem;color:#8A9BAE;margin-bottom:5px">
                <span>Progresso da revisão</span>
                <b style="color:{bar_color}">{pct}%</b>
            </div>
            <div style="background:rgba(255,255,255,0.07);border-radius:999px;
                        height:7px;overflow:hidden">
                <div style="width:{pct}%;height:100%;border-radius:999px;
                            background:{bar_color}"></div>
            </div>
        </div>
    </div>""", unsafe_allow_html=True)


def _render_kpis(tarefas: list[dict]) -> int:
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
    c1.metric("% Concluído", f"{pct}%", help="Etapas D+R+M concluídas.")
    c2.metric("Total de tarefas", len(tarefas))
    c3.metric("⛔ Travadas", by_status.get("travado", 0),
              delta_color="inverse" if by_status.get("travado") else "off")
    c4.metric("⏳ Pendentes", by_status.get("pendente", 0))
    c5.metric("✅ Concluídas", by_status.get("concluido", 0))
    return pct


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
            "D": "●" if t.get("etapa_d") else "○",
            "R": "●" if t.get("etapa_r") else "○",
            "M": "●" if t.get("etapa_m") else "○",
            "Semana": t.get("semana"),
            "Observação": t.get("observacao") or "",
            "_order": {"travado": 0, "pendente": 1, "em_andamento": 2,
                       "concluido": 3, "nao_aplica": 4}.get(status_raw, 9),
        })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).sort_values("_order").drop(columns=["_order"])
    return df


def _render_historico_chart(historico: list[dict]) -> None:
    import plotly.express as px
    df = pd.DataFrame(historico)
    df = df[df["total"] > 0]
    if df.empty:
        st.caption("Nenhum dado em revisões anteriores.")
        return
    df["color"] = df["pct"].apply(_pct_bar_color)
    df["label"] = df["pct"].map(lambda v: f"{v}%")
    fig = px.bar(df, x="titulo", y="pct", text="label",
                 color="color", color_discrete_map="identity")
    fig.update_traces(textposition="outside", cliponaxis=False,
                      textfont=dict(color="#F5F5F5"))
    apply_dark_theme(fig, height=240)
    fig.update_layout(
        margin=dict(l=6, r=6, t=16, b=10),
        xaxis=dict(title="", tickfont=dict(color="#8A9BAE")),
        yaxis=dict(title="% Concluído", range=[0, 115],
                   tickfont=dict(color="#8A9BAE"),
                   title_font=dict(color="#8A9BAE")),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def render_equipamento_dashboard() -> None:
    equipamento_id = st.session_state.get("_equip_detail_id")

    if not equipamento_id:
        empty_state(
            icon="◫", title="Nenhum equipamento selecionado",
            description="Use a busca na sidebar para encontrar e abrir um equipamento.",
            action_label="Ir para o Início", action_key="equip_dash_goto_home",
            nav_to="Início",
        )
        return

    tenant_id = current_tenant_id()
    token = st.session_state.get("sb_access_token", "")
    token_key = hashlib.md5(token.encode()).hexdigest()[:8]

    equip = load_equipamento_detail(tenant_id, equipamento_id, token_key, token)
    if not equip:
        st.error("Equipamento não encontrado ou sem acesso.")
        if st.button("← Voltar", key="equip_dash_back_err"):
            st.session_state.pop("_equip_detail_id", None)
            st.session_state["__nav_to"] = "Início"
            st.rerun()
        return

    revisao_id = get_current_revisao()
    if not revisao_id:
        rev = load_revisao_ativa(tenant_id, token_key, token)
        revisao_id = str(rev["id"]) if rev else None

    tarefas = load_tarefas_equipamento(
        tenant_id, equipamento_id, revisao_id, token_key, token
    ) if revisao_id else []

    # Pct para o header
    total_steps = len(tarefas) * 3
    done_steps = sum(
        int(bool(t.get("etapa_d"))) + int(bool(t.get("etapa_r"))) + int(bool(t.get("etapa_m")))
        for t in tarefas
    )
    pct = round((done_steps / max(total_steps, 1)) * 100) if tarefas else 0

    # Botão voltar discreto
    if st.button("← Voltar", key="equip_dash_back_top", type="tertiary"):
        st.session_state.pop("_equip_detail_id", None)
        st.rerun()

    # Cabeçalho com card + barra de progresso
    _render_header(equip, pct)

    if not revisao_id:
        st.warning("Nenhuma revisão ativa encontrada.")
        return

    # Abas: Tarefas | Histórico
    tab_tarefas, tab_historico = st.tabs(["📋  Tarefas da revisão", "📊  Histórico"])

    with tab_tarefas:
        if not tarefas:
            st.info("Este equipamento não possui tarefas na revisão ativa.")
        else:
            _render_kpis(tarefas)
            st.divider()
            df = _build_tarefas_df(tarefas)
            if not df.empty:
                data_table(
                    df,
                    column_config={
                        "Status": st.column_config.TextColumn("Status", width="medium"),
                        "D": st.column_config.TextColumn("D", width="small", help="Diagnóstico"),
                        "R": st.column_config.TextColumn("R", width="small", help="Reparo"),
                        "M": st.column_config.TextColumn("M", width="small", help="Manutenção"),
                        "Semana": st.column_config.NumberColumn("Semana", width="small"),
                        "Observação": st.column_config.TextColumn("Observação", width="large"),
                    },
                )

    with tab_historico:
        historico = load_historico_revisoes(tenant_id, equipamento_id, token_key, token)
        if historico:
            _render_historico_chart(historico)
        else:
            st.info("Nenhuma revisão encerrada encontrada para este equipamento.")
