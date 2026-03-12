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
from src.domain.kpi import calc_global_kpis
from src.ui.core.empty_state import empty_state
from src.ui.core.styles import page_header
from src.utils.kpi_engine import get_group_kpis
from src.utils.mobile import is_mobile
from src.utils.nav import set_current_revisao
from src.utils.supabase_helpers import current_tenant_id, sb_for_user
from src.utils.ui_helpers import status_badge

from .transforms import (
    apply_filters,
    build_inteligencia,
    equipment_progress,
    fmt_date,
    group_progress,
    normalize_matriz_base,
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
def _load_base_cached(_tenant_id: str, _revisao_id: str, _ver: str = "0") -> tuple[list, list]:
    """Versao cacheada para mv_matriz_base e equipamentos."""
    sb = sb_for_user()
    try:
        raw = sb.table("mv_matriz_base").select("*").eq("tenant_id", _tenant_id).eq("revisao_id", _revisao_id).execute().data or []
    except Exception:
        raw = []
    try:
        eq_meta = sb.table("equipamentos").select("id,frota,modelo,departamento_id").eq("tenant_id", _tenant_id).eq("ativo", True).execute().data or []
    except Exception:
        eq_meta = []
    return raw, eq_meta


def _load_base(sb, tenant_id: str, revisao_id: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    ver = str(st.session_state.get("data_version", "0"))
    raw_list, eq_list = _load_base_cached(tenant_id, revisao_id, ver)
    raw = pd.DataFrame(raw_list)
    eq_meta = pd.DataFrame(eq_list)
    if not eq_meta.empty and "id" in eq_meta.columns:
        eq_meta = eq_meta.rename(columns={"id": "equipamento_id"})
    return raw, eq_meta


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
        ("% Concluído", f"{overall['pct']:.1f}%", None, "off", "Percentual global alinhado à mesma regra da Matriz/Home."),
        ("Concluidos",  overall["concl"],         None,                                   "off",     None),
        ("Em andamento",overall["andamento"],     None,                                   "off",     None),
        ("Pendentes",   overall["pend"],           None, "off", None),
        ("Travados",    overall["trav"],           None, "off", None),
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
def _fragment_grupos(base: pd.DataFrame, dept_map: dict, dep_scope_ids, group_kpis_df: pd.DataFrame | None = None) -> None:
    gdf = group_kpis_df.copy() if group_kpis_df is not None and not group_kpis_df.empty else group_progress(base)
    if gdf.empty:
        empty_state(icon="⊕", title="Sem dados de grupos", description="Nenhum grupo com tarefas para esta revisao.")
        return
    gdf = gdf.copy()
    gdf["Departamento"] = gdf["departamento_id"].map(dept_map).fillna("—")
    if dep_scope_ids and "departamento_id" in gdf.columns:
        gdf = gdf[gdf["departamento_id"].isin(dep_scope_ids)]
    gdf["pct_concluido"] = pd.to_numeric(gdf["pct_concluido"], errors="coerce").fillna(0).clip(0, 100)
    display = gdf[["grupo", "Departamento", "pct_concluido"]].rename(columns={"grupo": "Grupo", "pct_concluido": "% Concluído"}).sort_values("% Concluído", ascending=False)
    st.bar_chart(display.set_index("Grupo")[["% Concluído"]], horizontal=True, use_container_width=True)
    st.dataframe(
        display, use_container_width=True, hide_index=True,
        column_config={"% Concluído": st.column_config.ProgressColumn("% Concluído", min_value=0, max_value=100, format="%.1f%%")},
    )


@st.fragment
def _fragment_setores(base: pd.DataFrame) -> None:
    sdf = sector_progress(base)
    if sdf.empty:
        st.info("Sem dados de setores.")
        return
    display = sdf.rename(columns={"setor": "Setor", "pct_concluido": "% Concluído"}).sort_values("% Concluído", ascending=False)
    st.bar_chart(display.set_index("Setor")[["% Concluído"]], horizontal=True, use_container_width=True)
    st.dataframe(
        display[["Setor", "% Concluído"]], use_container_width=True, hide_index=True,
        column_config={"% Concluído": st.column_config.ProgressColumn("% Concluído", min_value=0, max_value=100, format="%.1f%%")},
    )


@st.fragment
def _fragment_equipamentos(base: pd.DataFrame, dept_map: dict) -> None:
    edf = equipment_progress(base)
    if edf.empty:
        st.info("Sem dados de equipamentos.")
        return
    edf = edf.copy()
    edf["Departamento"] = edf["departamento_id"].map(dept_map).fillna("—")

    busca = st.text_input("Buscar frota / modelo", placeholder="Ex.: 2055, JD 6190…", key="dash_busca_eq")
    if busca.strip():
        mask = edf["Frota"].astype(str).str.lower().str.contains(busca.lower(), na=False) | edf["Modelo"].astype(str).str.lower().str.contains(busca.lower(), na=False)
        edf = edf[mask]

    edf["% Concluído"] = pd.to_numeric(edf["% Concluído"], errors="coerce").fillna(0).clip(0, 100)
    chart_source = edf.sort_values("% Concluído", ascending=False).head(15)
    if not chart_source.empty:
        st.bar_chart(chart_source.set_index("Frota")[["% Concluído"]], horizontal=True, use_container_width=True)

    cols = ["Frota", "Modelo", "Departamento", "Total", "% Concluído", "Pendentes", "Em andamento", "Travados", "Não aplica", "Concluídos"]
    present = [c for c in cols if c in edf.columns]
    st.dataframe(
        edf[present].sort_values("% Concluído", ascending=False),
        use_container_width=True, hide_index=True,
        column_config={"% Concluído": st.column_config.ProgressColumn("% Concluído", min_value=0, max_value=100, format="%.1f%%")},
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
    plot_df = tl.copy().sort_values("dia")
    plot_df["dia"] = pd.to_datetime(plot_df["dia"], errors="coerce").dt.strftime("%d/%m")
    st.bar_chart(plot_df.set_index("dia")[["movimentacoes"]], use_container_width=True)
    st.dataframe(
        tl.sort_values("dia", ascending=False),
        use_container_width=True, hide_index=True,
        column_config={
            "dia": st.column_config.DateColumn("Data", format="DD/MM/YYYY"),
            "movimentacoes": st.column_config.NumberColumn("Movimentações"),
            "concluidos": st.column_config.NumberColumn("Concluídos"),
            "restantes": st.column_config.NumberColumn("Restantes"),
        },
    )


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
        raw, eq_meta  = _load_base(sb, tenant_id, revisao_id)
        departamentos = _load_departamentos(tenant_id, ver)
        grupos = _load_grupos(tenant_id, ver)

    dept_map = {d["id"]: d.get("nome", "—") for d in departamentos if d.get("id")}
    gid_to_name = {g["id"]: g.get("nome", "—") for g in grupos if g.get("id")}
    gid_to_dept = {g["id"]: g.get("departamento_id") for g in grupos if g.get("id")}

    # Normaliza base
    base = normalize_matriz_base(raw, eq_meta)

    if base.empty:
        st.info(
            "Sem dados de execução para esta revisão. "
            "Verifique se a materialized view `mv_matriz_base` está populada."
        )
        return

    # Filtros de escopo
    base = apply_filters(base, dep_scope_ids, grp_scope_ids)

    # KPIs globais alinhados com Home/Matriz
    group_kpis_df = get_group_kpis(tenant_id, revisao_id, ver)
    if group_kpis_df is None or group_kpis_df.empty:
        overall = overall_from_base(base)
        dashboard_groups = group_progress(base)
    else:
        if grp_scope_ids:
            group_kpis_df = group_kpis_df[group_kpis_df["grupo_id"].isin(grp_scope_ids)]
        if dep_scope_ids:
            group_kpis_df = group_kpis_df[group_kpis_df["grupo_id"].map(gid_to_dept).isin(dep_scope_ids)]
        overall_calc = calc_global_kpis(group_kpis_df)
        overall = {
            "pct": float(overall_calc.get("pct", 0)),
            "concl": int((base["state"] == "concluido").sum()) if not base.empty else 0,
            "andamento": int((base["state"] == "em_andamento").sum()) if not base.empty else 0,
            "pend": int((base["state"] == "pendente").sum()) if not base.empty else 0,
            "trav": int((base["state"] == "travado").sum()) if not base.empty else 0,
        }
        dashboard_groups = group_kpis_df.copy()
        dashboard_groups["grupo"] = dashboard_groups["grupo_id"].map(gid_to_name).fillna("—")
        dashboard_groups["departamento_id"] = dashboard_groups["grupo_id"].map(gid_to_dept)
        dashboard_groups["pct_concluido"] = pd.to_numeric(dashboard_groups.get("pct", 0), errors="coerce").fillna(0).clip(0, 100)
    _fragment_kpis_globais(overall)

    st.divider()

    # Inteligência
    with st.spinner("", show_time=False):
        risco, previsao, heat, crit, tl = build_inteligencia(base)

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
        _fragment_grupos(base, dept_map, dep_scope_ids, dashboard_groups)

    elif active == "🔧 Equipamentos":
        st.markdown("### Progresso por equipamento")
        _fragment_equipamentos(base, dept_map)

    elif active == "📋 Setores":
        st.markdown("### Progresso por setor")
        _fragment_setores(base)

    elif active == "🌡️ Heatmap":
        st.markdown("### Heatmap de risco — Grupo × Setor")
        _fragment_heatmap(heat)

    elif active == "⚠️ Criticidade":
        st.markdown("### Top equipamentos críticos")
        _fragment_criticidade(crit)

    else:
        st.markdown("### Timeline de movimentações")
        _fragment_timeline(tl)
