"""Dashboard — camada de renderização.

Responsabilidade: exibir KPIs, progresso por grupo/setor/equipamento,
heatmap de risco e timeline de movimentação, tudo a partir dos dados
calculados em transforms.py.

Padrões Streamlit 1.42+:
  - @st.fragment para seções independentes
  - st.metric nativo para KPIs
  - st.status para carregamento granular
  - st.segmented_control para filtros compactos
  - st.dataframe com ProgressColumn
"""
from __future__ import annotations

import time

import pandas as pd
import plotly.express as px
import streamlit as st

from src.auth.scope import get_my_scope
from src.ui.core.empty_state import empty_state
from src.ui.core.styles import page_header
from src.utils.mobile import is_mobile
from src.utils.nav import get_current_revisao, set_current_revisao
from src.utils.supabase_helpers import current_tenant_id, sb_for_user
from src.utils.ui_helpers import status_badge

from .transforms import (
    apply_filters,
    build_inteligencia,
    build_progress_meta,
    equipment_progress,
    fmt_date,
    group_progress,
    normalize_matriz_base,
    normalize_task_base,
    overall_from_base,
    sector_progress,
)


# ── Helpers de carregamento ───────────────────────────────────────────────────

def _load_revisao(sb, tenant_id: str) -> dict | None:
    rows = (
        sb.table("revisoes")
        .select("id,titulo,status,data_inicio,data_fim,semanas_total")
        .eq("tenant_id", tenant_id)
        .eq("status", "ativa")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
    ) or []
    return rows[0] if rows else None


@st.cache_data(ttl=30, show_spinner=False)
def _load_base_cached(_tenant_id: str, _revisao_id: str, _ver: str = "0") -> tuple[list, list, list, list, list]:
    """Carrega a base do dashboard priorizando tarefas_servico (fonte exata).

    Ordem de preferência:
      1) tarefas_servico + grupo_servicos + equipamentos + grupos
      2) fallback para mv_matriz_base
    """
    sb = sb_for_user()

    try:
        eq_meta = (
            sb.table("equipamentos")
            .select("id,grupo_id,frota,modelo,departamento_id")
            .eq("tenant_id", _tenant_id)
            .eq("ativo", True)
            .execute()
            .data or []
        )
    except Exception:
        eq_meta = []

    try:
        grupos = (
            sb.table("equip_grupos")
            .select("id,nome,departamento_id")
            .eq("tenant_id", _tenant_id)
            .eq("ativo", True)
            .execute()
            .data or []
        )
    except Exception:
        grupos = []

    try:
        grupo_servicos = (
            sb.table("grupo_servicos")
            .select("grupo_id,servico_id,servicos(setor)")
            .eq("tenant_id", _tenant_id)
            .execute()
            .data or []
        )
    except Exception:
        grupo_servicos = []

    try:
        tarefas = (
            sb.table("tarefas_servico")
            .select("equipamento_id,servico_id,status,updated_at,etapa_d,etapa_r,etapa_m,servicos(setor)")
            .eq("tenant_id", _tenant_id)
            .eq("revisao_id", _revisao_id)
            .execute()
            .data or []
        )
    except Exception:
        tarefas = []

    try:
        raw_mv = sb.table("mv_matriz_base").select("*").eq("tenant_id", _tenant_id).eq("revisao_id", _revisao_id).execute().data or []
    except Exception:
        raw_mv = []

    return tarefas, raw_mv, eq_meta, grupos, grupo_servicos


def _load_base(sb, tenant_id: str, revisao_id: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    ver = str(st.session_state.get("data_version", "0"))
    tarefas_list, raw_mv_list, eq_list, grupos_list, grupo_servicos_list = _load_base_cached(tenant_id, revisao_id, ver)

    eq_meta = pd.DataFrame(eq_list)
    if not eq_meta.empty and "id" in eq_meta.columns:
        eq_meta = eq_meta.rename(columns={"id": "equipamento_id"})

    grupos_df = pd.DataFrame(grupos_list)

    gs_df = pd.DataFrame(grupo_servicos_list)
    if not gs_df.empty and "servicos" in gs_df.columns:
        gs_df["setor"] = gs_df["servicos"].apply(lambda s: (s or {}).get("setor") if isinstance(s, dict) else None)

    tarefas_df = pd.DataFrame(tarefas_list)
    if not tarefas_df.empty and "servicos" in tarefas_df.columns:
        tarefas_df["setor"] = tarefas_df["servicos"].apply(lambda s: (s or {}).get("setor") if isinstance(s, dict) else None)

    if not tarefas_df.empty:
        base = normalize_task_base(tarefas_df, eq_meta, grupos_df)
    else:
        raw = pd.DataFrame(raw_mv_list)
        base = normalize_matriz_base(raw, eq_meta)

    return base, eq_meta, grupos_df, gs_df


@st.cache_data(ttl=120, show_spinner=False)
def _load_departamentos(_tenant_id: str, _ver: str = "0") -> list[dict]:
    sb = sb_for_user()
    try:
        return (
            sb.table("departamentos")
            .select("id,nome")
            .eq("tenant_id", _tenant_id)
            .eq("ativo", True)
            .order("nome")
            .execute()
            .data or []
        )
    except Exception:
        return []


@st.cache_data(ttl=120, show_spinner=False)
def _load_grupos(_tenant_id: str, _ver: str = "0") -> list[dict]:
    sb = sb_for_user()
    try:
        return (
            sb.table("equip_grupos")
            .select("id,nome,departamento_id")
            .eq("tenant_id", _tenant_id)
            .eq("ativo", True)
            .order("nome")
            .execute()
            .data or []
        )
    except Exception:
        return []


# ── Fragments de renderização ─────────────────────────────────────────────────

@st.fragment
def _fragment_kpis_globais(overall: dict) -> None:
    from src.utils.ui_helpers import mobile_columns
    cols = mobile_columns(5, 2)
    labels = [
        ("% Concluido", f"{overall['pct']}%",   None,                                   "off",     "Percentual global calculado por etapas ok / (total x 3)."),
        ("Concluidos",  overall["concl"],         None,                                   "off",     None),
        ("Em andamento",overall["andamento"],     None,                                   "off",     None),
        ("Pendentes",   overall["pend"],           f"-{overall['pend']}" if overall["pend"] else "0",  "inverse" if overall["pend"] else "off", None),
        ("Travados",    overall["trav"],           f"-{overall['trav']}" if overall["trav"] else "0",  "inverse" if overall["trav"] else "off", None),
    ]
    for i, (label, value, delta, delta_color, help_text) in enumerate(labels):
        with cols[i % len(cols)]:
            st.metric(label, value, delta=delta, delta_color=delta_color, help=help_text)


@st.fragment
def _fragment_previsao(previsao: dict, risco: dict) -> None:
    _RISCO_ICONS = {"baixo": "🟢", "medio": "🟡", "alto": "🔴"}
    _PREV_LABELS = {"no_prazo": "✅ No prazo", "atraso": "⚠️ Com atraso", "sem_base": "— Sem base"}
    status_prev  = previsao.get("status_previsao", "sem_base")
    status_risco = risco.get("status_risco", "baixo")

    c1, c2 = st.columns(2)
    with c1:
        st.metric("Previsão de término", _PREV_LABELS.get(status_prev, status_prev))
        if previsao.get("previsao_termino"):
            st.caption(f"Data estimada: **{fmt_date(previsao['previsao_termino'])}**")
        if previsao.get("data_fim_planejada"):
            st.caption(f"Data planejada: **{fmt_date(previsao['data_fim_planejada'])}**")
    with c2:
        icon = _RISCO_ICONS.get(status_risco, "⚪")
        st.metric(f"{icon} Risco operacional", f"{risco.get('risco_score', 0):.1f}",
                  help="Score: travados × 3 + pendentes × 1.5 + em_andamento × 1.")
        st.caption(
            f"Ritmo: **{previsao.get('ritmo_medio_dia', 0):.2f}%/dia** | "
            f"Dias passados: **{previsao.get('dias_passados', 0)}** | "
            f"Dias rest. est.: **{previsao.get('dias_restantes_estimados', 0):.0f}**"
        )


@st.fragment
def _fragment_grupos(base: pd.DataFrame, dept_map: dict, dep_scope_ids, progress_meta: dict) -> None:
    gdf = group_progress(base, progress_meta)
    if gdf.empty:
        empty_state(icon="⊕", title="Sem dados de grupos", description="Nenhum grupo com tarefas para esta revisao.")
        return
    gdf = gdf.copy()
    gdf["Departamento"] = gdf["departamento_id"].map(dept_map).fillna("—")
    if dep_scope_ids and "departamento_id" in gdf.columns:
        gdf = gdf[gdf["departamento_id"].isin(dep_scope_ids)]
    display = (
        gdf[["grupo", "Departamento", "pct_concluido"]]
        .rename(columns={"grupo": "Grupo", "pct_concluido": "% Concluído"})
        .sort_values("% Concluído", ascending=False)
    )
    st.dataframe(
        display, use_container_width=True, hide_index=True,
        column_config={"% Concluído": st.column_config.ProgressColumn(
            "% Concluído", min_value=0, max_value=100)},
    )


@st.fragment
def _fragment_setores(base: pd.DataFrame, progress_meta: dict) -> None:
    sdf = sector_progress(base, progress_meta)
    if sdf.empty:
        st.info("Sem dados de setores.")
        return
    display = sdf.rename(
        columns={"setor": "Setor", "pct_concluido": "% Concluído"}
    ).sort_values("% Concluído", ascending=False)
    st.dataframe(
        display, use_container_width=True, hide_index=True,
        column_config={"% Concluído": st.column_config.ProgressColumn(
            "% Concluído", min_value=0, max_value=100)},
    )


@st.fragment
def _fragment_equipamentos(base: pd.DataFrame, dept_map: dict, progress_meta: dict) -> None:
    edf = equipment_progress(base, progress_meta)
    if edf.empty:
        st.info("Sem dados de equipamentos.")
        return
    edf = edf.copy()
    edf["Departamento"] = edf["departamento_id"].map(dept_map).fillna("—")

    busca = st.text_input("Buscar frota / modelo", placeholder="Ex.: 2055, JD 6190…",
                          key="dash_busca_eq")
    if busca.strip():
        mask = (
            edf["Frota"].str.lower().str.contains(busca.lower(), na=False)
            | edf["Modelo"].str.lower().str.contains(busca.lower(), na=False)
        )
        edf = edf[mask]

    cols = ["Frota", "Modelo", "Departamento", "Total", "% Concluído",
            "Pendentes", "Em andamento", "Travados", "Não aplica", "Concluídos"]
    present = [c for c in cols if c in edf.columns]
    st.dataframe(
        edf[present].sort_values("% Concluído", ascending=False),
        use_container_width=True, hide_index=True,
        column_config={"% Concluído": st.column_config.ProgressColumn(
            "% Concluído", min_value=0, max_value=100)},
    )


@st.fragment
def _fragment_heatmap(heat: pd.DataFrame) -> None:
    if heat.empty:
        st.info("Sem dados de heatmap para esta revisão.")
        return
    fig = px.density_heatmap(
        heat, x="setor", y="grupo", z="calor_score",
        color_continuous_scale="RdYlGn_r",
        labels={"setor": "Setor", "grupo": "Grupo", "calor_score": "Score de risco"},
        title="Heatmap de Risco — Grupo × Setor",
    )
    fig.update_layout(
        height=max(300, len(heat["grupo"].unique()) * 40 + 80),
        margin=dict(l=10, r=10, t=40, b=10),
        paper_bgcolor="#06080B", plot_bgcolor="#0C111A",
        font=dict(color="#E8EDF5", family="DM Sans, sans-serif", size=11),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.markdown(
        """<div class="risk-legend">
          <span><span class="risk-dot risk-dot-low"></span>Baixo risco (0)</span>
          <span><span class="risk-dot risk-dot-mid"></span>Medio risco (1.5)</span>
          <span><span class="risk-dot risk-dot-high"></span>Alto risco (3+)</span>
        </div>""",
        unsafe_allow_html=True,
    )
    st.caption("Score: travado x 3 + pendente x 1.5 + em_andamento x 1, normalizado por total.")


@st.fragment
def _fragment_criticidade(crit: pd.DataFrame) -> None:
    if crit.empty:
        st.info("Sem equipamentos críticos para exibir.")
        return
    cols = ["ranking_criticidade", "Equipamento", "grupo",
            "criticidade_score", "travados", "pendentes", "pct_concluido"]
    present = [c for c in cols if c in crit.columns]
    st.dataframe(
        crit[present].head(20),
        use_container_width=True, hide_index=True,
        column_config={
            "ranking_criticidade": st.column_config.NumberColumn("#", width="small"),
            "criticidade_score":   st.column_config.NumberColumn("Score", format="%.2f"),
            "pct_concluido":       st.column_config.ProgressColumn(
                "% Concluído", min_value=0, max_value=100),
        },
    )


@st.fragment
def _fragment_timeline(tl: pd.DataFrame) -> None:
    if tl.empty:
        st.info("Sem movimentações registradas nesta revisão.")
        return
    fig = px.bar(
        tl, x="dia", y="movimentacoes",
        labels={"dia": "Data", "movimentacoes": "Movimentações"},
        title="Movimentações diárias",
    )
    fig.update_layout(
        height=300, margin=dict(l=10, r=10, t=40, b=10),
        paper_bgcolor="#06080B", plot_bgcolor="#0C111A",
        xaxis=dict(gridcolor="rgba(255,255,255,0.06)"),
        yaxis=dict(gridcolor="rgba(255,255,255,0.06)"),
        font=dict(color="#E8EDF5", family="DM Sans, sans-serif", size=11),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ── Retrocompatibilidade: render_kpis simples ─────────────────────────────────

def render_kpis(data: dict) -> None:
    """Renderiza KPIs a partir de um dict simples (retrocompatibilidade)."""
    total        = data.get("total", 0)
    concluidos   = data.get("concluidos", 0)
    atrasados    = data.get("atrasados", 0)
    equipamentos = data.get("equipamentos", 0)
    pct = round((concluidos / total) * 100) if total else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Progresso",    f"{pct}%")
    col2.metric("Concluídos",   concluidos)
    col3.metric("Atrasados",    atrasados)
    col4.metric("Equipamentos", equipamentos)


# ── Ponto de entrada público ──────────────────────────────────────────────────

def render_dashboard() -> None:
    """Ponto de entrada do Dashboard — orquestra carregamento e fragments."""
    page_header("Dashboard")

    tenant_id = current_tenant_id()
    if not tenant_id:
        st.info("Selecione um tenant para ver o dashboard.")
        return

    sb = sb_for_user()
    dep_scope_ids, grp_scope_ids = get_my_scope(tenant_id)

    # Revisão ativa
    with st.spinner("", show_time=False):
        rev = _load_revisao(sb, tenant_id)

    if not rev:
        st.warning("Nenhuma revisão ativa encontrada para este tenant.")
        return

    revisao_id = rev["id"]
    set_current_revisao(revisao_id)
    st.session_state["_sidebar_rev_titulo"] = rev.get("titulo")

    # Header
    h1, h2 = st.columns([0.82, 0.18])
    with h1:
        st.markdown(f"## {rev.get('titulo', 'Revisão')}")
        status_badge(rev.get("status"))
    with h2:
        if st.button("Atualizar", icon=":material/refresh:",
                     use_container_width=True, key="dash_refresh_btn"):
            st.session_state["data_version"] = str(time.time())
            st.toast("Atualizado", icon=":material/refresh:")
            st.rerun()

    # Carregamento de dados
    ver = str(st.session_state.get("data_version", "0"))
    with st.spinner("", show_time=False):
        base, eq_meta, grupos_df, grupo_servicos_df = _load_base(sb, tenant_id, revisao_id)
        departamentos = _load_departamentos(tenant_id, ver)

    dept_map = {d["id"]: d.get("nome", "—") for d in departamentos if d.get("id")}

    if base.empty:
        st.info(
            "Sem dados de execução para esta revisão. "
            "Verifique se a materialized view `mv_matriz_base` está populada."
        )
        return

    # Filtros de escopo
    base = apply_filters(base, dep_scope_ids, grp_scope_ids)
    progress_meta = build_progress_meta(eq_meta, grupo_servicos_df, base)

    # KPIs globais
    overall = overall_from_base(base, progress_meta)
    _fragment_kpis_globais(overall)

    st.divider()

    # Inteligência
    with st.spinner("", show_time=False):
        risco, previsao, heat, crit, tl = build_inteligencia(base, progress_meta)

    _fragment_previsao(previsao, risco)

    st.divider()

    # Tabs principais
    _TABS = [
        "🏗️ Grupos", "🔧 Equipamentos", "📋 Setores",
        "🌡️ Heatmap", "⚠️ Criticidade", "📈 Timeline",
    ]

    def _on_tab_change() -> None:
        st.session_state["_dash_tab"] = st.session_state["_dash_tab_ctrl"]

    active = st.session_state.get("_dash_tab", _TABS[0])
    if active not in _TABS:
        active = _TABS[0]

    st.segmented_control(
        "Visão", _TABS, default=active,
        key="_dash_tab_ctrl", on_change=_on_tab_change,
        label_visibility="collapsed",
    )
    active = st.session_state.get("_dash_tab", _TABS[0])

    if active == "🏗️ Grupos":
        st.markdown("### Progresso por grupo")
        _fragment_grupos(base, dept_map, dep_scope_ids, progress_meta)

    elif active == "🔧 Equipamentos":
        st.markdown("### Progresso por equipamento")
        _fragment_equipamentos(base, dept_map, progress_meta)

    elif active == "📋 Setores":
        st.markdown("### Progresso por setor")
        _fragment_setores(base, progress_meta)

    elif active == "🌡️ Heatmap":
        st.markdown("### Heatmap de risco — Grupo × Setor")
        _fragment_heatmap(heat)

    elif active == "⚠️ Criticidade":
        st.markdown("### Top equipamentos críticos")
        _fragment_criticidade(crit)

    else:
        st.markdown("### Timeline de movimentações")
        _fragment_timeline(tl)
