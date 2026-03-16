"""Dashboard — camada de renderização.

Responsabilidade: exibir KPIs, progresso por grupo/setor/equipamento,
heatmap de risco e timeline de movimentação, tudo a partir dos dados
calculados em transforms.py.
"""
from __future__ import annotations

import time

import pandas as pd
import plotly.express as px
import streamlit as st

from src.auth.scope import get_my_scope
from src.domain.kpi import calc_global_kpis
from src.ui.core.empty_state import empty_state
from src.ui.components.filters import multiselect_departamentos, multiselect_grupos
from src.ui.core.styles import page_header
from src.ui.core.cache import bump_data_version
from src.utils.kpi_engine import get_group_kpis
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
def _load_base_cached(_tenant_id: str, _revisao_id: str,
                      _ver: str = "0") -> tuple[list, list]:
    sb = sb_for_user()
    try:
        raw = sb.table("mv_matriz_base").select("*").eq("tenant_id",
                                                        _tenant_id).eq("revisao_id", _revisao_id).execute().data or []
    except Exception:
        raw = []
    try:
        eq_meta = sb.table("equipamentos").select("id,frota,modelo,departamento_id").eq(
            "tenant_id", _tenant_id).eq("ativo", True).execute().data or []
    except Exception:
        eq_meta = []
    return raw, eq_meta


def _load_base(sb,
               tenant_id: str,
               revisao_id: str) -> tuple[pd.DataFrame,
                                         pd.DataFrame]:
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


def _render_pct_rank_chart(
        df: pd.DataFrame,
        category_col: str,
        value_col: str,
        title: str,
        top_n: int = 10) -> None:
    chart_df = df.copy()
    if chart_df.empty or category_col not in chart_df.columns or value_col not in chart_df.columns:
        st.info("Sem dados para exibir.")
        return

    chart_df = chart_df[[category_col, value_col]].copy()
    chart_df[category_col] = chart_df[category_col].fillna("—").astype(str)
    chart_df[value_col] = pd.to_numeric(
        chart_df[value_col],
        errors="coerce").fillna(0).clip(
        0,
        100)
    chart_df = chart_df.sort_values(value_col, ascending=False).head(top_n)
    chart_df = chart_df.sort_values(value_col, ascending=True)
    chart_df["label"] = chart_df[value_col].map(lambda v: f"{v:.1f}%")

    fig = px.bar(
        chart_df,
        x=value_col,
        y=category_col,
        orientation="h",
        text="label",
        title=title,
    )
    fig.update_traces(
        textposition="outside",
        cliponaxis=False,
        hovertemplate="%{y}<br>% Concluído: %{x:.1f}%<extra></extra>")
    fig.update_layout(
        height=max(380, 42 * len(chart_df) + 80),
        margin=dict(l=10, r=90, t=48, b=10),
        xaxis=dict(range=[0, 100], title="% Concluído"),
        yaxis=dict(title="", type="category"),
        paper_bgcolor="#06080B",
        plot_bgcolor="#0C111A",
        font=dict(color="#E8EDF5", family="DM Sans, sans-serif", size=11),
        showlegend=False,
    )
    st.plotly_chart(
        fig, use_container_width=True, config={
            "displayModeBar": False})


@st.fragment
def _fragment_kpis_globais(overall: dict) -> None:
    from src.utils.ui_helpers import mobile_columns
    cols = mobile_columns(5, 2)
    labels = [("% Concluído",
               f"{overall['pct']:.1f}%",
               None,
               "off",
               "Percentual global alinhado à mesma regra da Matriz/Home."),
              ("Concluídos",
               overall["concl"],
               None,
               "off",
               None),
              ("Em andamento",
               overall["andamento"],
               None,
               "off",
               None),
              ("Pendentes",
               overall["pend"],
               None,
               "off",
               None),
              ("Travados",
               overall["trav"],
               None,
               "off",
               None),
              ]
    for i, (label, value, delta, delta_color, help_text) in enumerate(labels):
        with cols[i % len(cols)]:
            st.metric(
                label,
                value,
                delta=delta,
                delta_color=delta_color,
                help=help_text)


@st.fragment
def _fragment_previsao(previsao: dict, risco: dict) -> None:
    _RISCO_ICONS = {"baixo": "🟢", "medio": "🟡", "alto": "🔴"}
    _PREV_LABELS = {
        "no_prazo": "✅ No prazo",
        "atraso": "⚠️ Com atraso",
        "sem_base": "— Sem base"}
    status_prev = previsao.get("status_previsao", "sem_base")
    status_risco = risco.get("status_risco", "baixo")

    c1, c2 = st.columns(2)
    with c1:
        st.metric(
            "Previsão de término",
            _PREV_LABELS.get(
                status_prev,
                status_prev))
        if previsao.get("previsao_termino"):
            st.caption(
                f"Data estimada: **{fmt_date(previsao['previsao_termino'])}**")
        if previsao.get("data_fim_planejada"):
            st.caption(
                f"Data planejada: **{fmt_date(previsao['data_fim_planejada'])}**")
    with c2:
        icon = _RISCO_ICONS.get(status_risco, "⚪")
        st.metric(
            f"{icon} Risco operacional",
            f"{risco.get('risco_score', 0):.1f}",
            help="Score: travados × 3 + pendentes × 1.5 + em_andamento × 1.",
        )
        st.caption(
            f"Ritmo: **{previsao.get('ritmo_medio_dia', 0):.2f}%/dia** | "
            f"Dias passados: **{previsao.get('dias_passados', 0)}** | "
            f"Dias rest. est.: **{previsao.get('dias_restantes_estimados', 0):.0f}**"
        )


@st.fragment
def _fragment_grupos(
        base: pd.DataFrame,
        dept_map: dict,
        group_kpis_df: pd.DataFrame | None = None,
        top_n: int = 10) -> None:
    gdf = group_kpis_df.copy(
    ) if group_kpis_df is not None and not group_kpis_df.empty else group_progress(base)
    if gdf.empty:
        empty_state(icon="⊕", title="Sem dados de grupos",
                    description="Nenhum grupo com tarefas para esta revisão.")
        return
    gdf = gdf.copy()
    gdf["Departamento"] = gdf["departamento_id"].map(dept_map).fillna("—")
    gdf["pct_concluido"] = pd.to_numeric(
        gdf["pct_concluido"],
        errors="coerce").fillna(0).clip(
        0,
        100)
    display = (
        gdf[["grupo", "Departamento", "pct_concluido"]]
        .rename(columns={"grupo": "Grupo", "pct_concluido": "% Concluído"})
        .sort_values(["% Concluído", "Grupo"], ascending=[False, True])
    )
    _render_pct_rank_chart(
        display,
        "Grupo",
        "% Concluído",
        f"Top {top_n} grupos por % de conclusão",
        top_n=top_n)
    st.dataframe(
        display.head(top_n),
        use_container_width=True,
        hide_index=True,
        column_config={
            "% Concluído": st.column_config.ProgressColumn(
                "% Concluído",
                min_value=0,
                max_value=100,
                format="%.1f%%")},
    )
    with st.expander("⬇ Exportar", expanded=False):
        from src.utils.ui_helpers import df_to_xlsx
        _exp = display.copy()
        col_csv, col_xlsx = st.columns(2)
        with col_csv:
            st.download_button(
                "CSV", _exp.to_csv(index=False).encode("utf-8"),
                file_name="dashboard_grupos.csv", mime="text/csv",
                use_container_width=True, key="dash_grupos_csv",
            )
        with col_xlsx:
            st.download_button(
                "XLSX",
                df_to_xlsx(
                    _exp,
                    sheet_name="Grupos"),
                file_name="dashboard_grupos.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="dash_grupos_xlsx",
            )


@st.fragment
def _fragment_setores(base: pd.DataFrame, top_n: int = 10) -> None:
    sdf = sector_progress(base)
    if sdf.empty:
        st.info("Sem dados de setores.")
        return
    display = sdf.rename(columns={"setor": "Setor", "pct_concluido": "% Concluído"}).sort_values(
        ["% Concluído", "Setor"], ascending=[False, True])
    _render_pct_rank_chart(
        display,
        "Setor",
        "% Concluído",
        f"Top {top_n} setores por % de conclusão",
        top_n=top_n)
    st.dataframe(
        display.head(top_n)[
            [
                "Setor",
                "% Concluído"]],
        use_container_width=True,
        hide_index=True,
        column_config={
            "% Concluído": st.column_config.ProgressColumn(
                "% Concluído",
                min_value=0,
                max_value=100,
                format="%.1f%%")},
    )
    with st.expander("⬇ Exportar", expanded=False):
        from src.utils.ui_helpers import df_to_xlsx
        _exp = display[["Setor", "% Concluído"]].copy()
        col_csv, col_xlsx = st.columns(2)
        with col_csv:
            st.download_button(
                "CSV", _exp.to_csv(index=False).encode("utf-8"),
                file_name="dashboard_setores.csv", mime="text/csv",
                use_container_width=True, key="dash_setores_csv",
            )
        with col_xlsx:
            st.download_button(
                "XLSX",
                df_to_xlsx(
                    _exp,
                    sheet_name="Setores"),
                file_name="dashboard_setores.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="dash_setores_xlsx",
            )


@st.fragment
def _fragment_equipamentos(
        base: pd.DataFrame,
        dept_map: dict,
        top_n: int = 10) -> None:
    edf = equipment_progress(base)
    if edf.empty:
        st.info("Sem dados de equipamentos.")
        return

    edf = edf.copy()
    edf["Frota"] = edf["Frota"].fillna("—").astype(str).str.strip()
    edf["Modelo"] = edf["Modelo"].fillna("—").astype(str).str.strip()
    edf["Departamento"] = edf["departamento_id"].map(dept_map).fillna("—")

    busca = st.text_input(
        "Buscar frota / modelo",
        placeholder="Ex.: 2055, JD 6190…",
        key="dash_busca_eq")
    if busca.strip():
        termo = busca.strip().lower()
        mask = (
            edf["Frota"].str.lower().str.contains(termo, na=False)
            | edf["Modelo"].str.lower().str.contains(termo, na=False)
        )
        edf = edf[mask]

    if edf.empty:
        st.info("Nenhum equipamento encontrado para o filtro informado.")
        return

    # Consolida por código de frota + modelo para evitar linhas duplicadas no
    # gráfico.
    agg = (edf.groupby(["Frota",
                        "Modelo",
                        "Departamento"],
                       dropna=False,
                       as_index=False) .agg({"Total": "sum",
                                             "Pendentes": "sum",
                                             "Em andamento": "sum",
                                             "Travados": "sum",
                                             "Não aplica": "sum",
                                             "Concluídos": "sum",
                                             "done_steps": "sum",
                                             "expected_steps": "sum",
                                             }))
    agg["% Concluído"] = (
        (pd.to_numeric(
            agg["done_steps"],
            errors="coerce").fillna(0) /
            pd.to_numeric(
            agg["expected_steps"],
            errors="coerce").replace(
                0,
                pd.NA)) *
        100).fillna(0).clip(
        0,
        100).round(1)
    agg["Equipamento"] = agg.apply(
        lambda r: f"{
            r['Frota']} — {
            r['Modelo']}" if str(
                r["Modelo"]).strip() not in {
                    "",
                    "—"} else str(
                        r["Frota"]),
        axis=1,
    )

    rank_df = agg.sort_values(["% Concluído", "Concluídos", "Equipamento"], ascending=[
                              False, False, True]).head(top_n)
    _render_pct_rank_chart(
        rank_df,
        "Equipamento",
        "% Concluído",
        f"Top {top_n} equipamentos por % de conclusão",
        top_n=top_n)

    cols = [
        "Equipamento",
        "Frota",
        "Modelo",
        "Departamento",
        "Total",
        "% Concluído",
        "Pendentes",
        "Em andamento",
        "Travados",
        "Não aplica",
        "Concluídos"]
    st.dataframe(
        rank_df[cols],
        use_container_width=True,
        hide_index=True,
        column_config={
            "% Concluído": st.column_config.ProgressColumn(
                "% Concluído",
                min_value=0,
                max_value=100,
                format="%.1f%%")},
    )
    with st.expander("⬇ Exportar tabela completa", expanded=False):
        from src.utils.ui_helpers import df_to_xlsx
        _exp = agg[cols].sort_values(
            ["% Concluído", "Equipamento"], ascending=[False, True])
        col_csv, col_xlsx = st.columns(2)
        with col_csv:
            st.download_button(
                "CSV", _exp.to_csv(index=False).encode("utf-8"),
                file_name="dashboard_equipamentos.csv", mime="text/csv",
                use_container_width=True, key="dash_eq_csv",
            )
        with col_xlsx:
            st.download_button(
                "XLSX",
                df_to_xlsx(
                    _exp,
                    sheet_name="Equipamentos"),
                file_name="dashboard_equipamentos.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="dash_eq_xlsx",
            )


@st.fragment
def _fragment_heatmap(heat: pd.DataFrame) -> None:
    if heat.empty:
        st.info("Sem dados de heatmap para esta revisão.")
        return
    fig = px.density_heatmap(
        heat,
        x="setor",
        y="grupo",
        z="calor_score",
        color_continuous_scale="RdYlGn_r",
        labels={
            "setor": "Setor",
            "grupo": "Grupo",
            "calor_score": "Score de risco"},
        title="Heatmap de Risco — Grupo × Setor",
    )
    fig.update_layout(
        height=max(300, len(heat["grupo"].unique()) * 40 + 80),
        margin=dict(l=10, r=10, t=40, b=10),
        paper_bgcolor="#06080B",
        plot_bgcolor="#0C111A",
        font=dict(color="#E8EDF5", family="DM Sans, sans-serif", size=11),
    )
    st.plotly_chart(
        fig, use_container_width=True, config={
            "displayModeBar": False})
    st.caption(
        "Score: travado × 3 + pendente × 1.5 + em_andamento × 1, normalizado por total.")


@st.fragment
def _fragment_criticidade(crit: pd.DataFrame) -> None:
    if crit.empty:
        st.info("Sem equipamentos críticos para exibir.")
        return
    cols = [
        "ranking_criticidade",
        "Equipamento",
        "grupo",
        "criticidade_score",
        "travados",
        "pendentes",
        "pct_concluido"]
    present = [c for c in cols if c in crit.columns]
    st.dataframe(
        crit[present].head(20), use_container_width=True, hide_index=True, column_config={
            "ranking_criticidade": st.column_config.NumberColumn(
                "#", width="small"), "criticidade_score": st.column_config.NumberColumn(
                "Score", format="%.2f"), "pct_concluido": st.column_config.ProgressColumn(
                    "% Concluído", min_value=0, max_value=100), }, )


@st.fragment
def _fragment_timeline(tl: pd.DataFrame) -> None:
    if tl.empty:
        st.info("Sem movimentações registradas nesta revisão.")
        return
    plot_df = tl.copy().sort_values("dia")
    plot_df["dia_label"] = pd.to_datetime(
        plot_df["dia"], errors="coerce").dt.strftime("%d/%m")
    fig = px.bar(
        plot_df,
        x="dia_label",
        y="movimentacoes",
        text="movimentacoes",
        title="Movimentações por dia")
    fig.update_traces(textposition="outside", cliponaxis=False)
    fig.update_layout(
        height=360,
        margin=dict(l=10, r=10, t=48, b=10),
        xaxis_title="Dia",
        yaxis_title="Movimentações",
        paper_bgcolor="#06080B",
        plot_bgcolor="#0C111A",
        font=dict(color="#E8EDF5", family="DM Sans, sans-serif", size=11),
        showlegend=False,
    )
    st.plotly_chart(
        fig, use_container_width=True, config={
            "displayModeBar": False})
    st.dataframe(
        tl.sort_values("dia", ascending=False),
        use_container_width=True,
        hide_index=True,
        column_config={
            "dia": st.column_config.DateColumn("Data", format="DD/MM/YYYY"),
            "movimentacoes": st.column_config.NumberColumn("Movimentações"),
            "concluidos": st.column_config.NumberColumn("Concluídos"),
            "restantes": st.column_config.NumberColumn("Restantes"),
        },
    )


def render_dashboard() -> None:
    page_header("Dashboard")

    tenant_id = current_tenant_id()
    if not tenant_id:
        st.info("Selecione um tenant para ver o dashboard.")
        return

    sb = sb_for_user()
    dep_scope_ids, grp_scope_ids = get_my_scope(tenant_id)

    with st.spinner("", show_time=False):
        rev = _load_revisao(sb, tenant_id)
    if not rev:
        st.warning("Nenhuma revisão ativa encontrada para este tenant.")
        return

    revisao_id = rev["id"]
    set_current_revisao(revisao_id)
    st.session_state["_sidebar_rev_titulo"] = rev.get("titulo")

    h1, h2 = st.columns([0.82, 0.18])
    with h1:
        st.markdown(f"## {rev.get('titulo', 'Revisão')}")
        status_badge(rev.get("status"))
    with h2:
        if st.button(
            "Atualizar",
            icon=":material/refresh:",
            use_container_width=True,
                key="dash_refresh_btn"):
            bump_data_version()
            st.toast("Atualizado", icon=":material/refresh:")
            st.rerun()

    ver = str(st.session_state.get("data_version", "0"))
    with st.spinner("", show_time=False):
        raw, eq_meta = _load_base(sb, tenant_id, revisao_id)
        departamentos = _load_departamentos(tenant_id, ver)
        grupos = _load_grupos(tenant_id, ver)

    dept_map = {d["id"]: d.get("nome", "—")
                for d in departamentos if d.get("id")}
    gid_to_name = {g["id"]: g.get("nome", "—") for g in grupos if g.get("id")}
    gid_to_dept = {g["id"]: g.get("departamento_id")
                   for g in grupos if g.get("id")}

    base = normalize_matriz_base(raw, eq_meta)
    if base.empty:
        st.info("Sem dados de execução para esta revisão. Verifique se a materialized view `mv_matriz_base` está populada.")
        return

    base = apply_filters(base, dep_scope_ids, grp_scope_ids)

    group_kpis_df = get_group_kpis(tenant_id, revisao_id, ver)
    if group_kpis_df is not None and not group_kpis_df.empty:
        if grp_scope_ids:
            group_kpis_df = group_kpis_df[group_kpis_df["grupo_id"].isin(
                grp_scope_ids)]
        if dep_scope_ids:
            group_kpis_df = group_kpis_df[group_kpis_df["grupo_id"].map(
                gid_to_dept).isin(dep_scope_ids)]
        dashboard_groups = group_kpis_df.copy()
        dashboard_groups["grupo"] = dashboard_groups["grupo_id"].map(
            gid_to_name).fillna("—")
        dashboard_groups["departamento_id"] = dashboard_groups["grupo_id"].map(
            gid_to_dept)
        dashboard_groups["pct_concluido"] = pd.to_numeric(
            dashboard_groups.get(
                "pct", 0), errors="coerce").fillna(0).clip(
            0, 100)
    else:
        dashboard_groups = group_progress(base)

    st.markdown("### Filtros")
    c1, c2, c3 = st.columns([1.1, 1.4, 0.6])
    with c1:
        dept_selected_ids = multiselect_departamentos(
            departamentos,
            key="dash_filter_dept",
            allowed_ids=dep_scope_ids,
        )

    with c2:
        group_selected_ids = multiselect_grupos(
            grupos,
            key="dash_filter_group",
            allowed_group_ids=grp_scope_ids,
            departamento_ids=dept_selected_ids,
        )
    top_n = int(
        c3.selectbox(
            "Top",
            options=[
                5,
                10,
                15],
            index=1,
            key="dash_filter_top"))

    base_filtered = apply_filters(base, dept_selected_ids, group_selected_ids)
    dashboard_groups_filtered = dashboard_groups.copy()
    if dept_selected_ids and "departamento_id" in dashboard_groups_filtered.columns:
        dashboard_groups_filtered = dashboard_groups_filtered[dashboard_groups_filtered["departamento_id"].isin(
            dept_selected_ids)]
    if group_selected_ids and "grupo_id" in dashboard_groups_filtered.columns:
        dashboard_groups_filtered = dashboard_groups_filtered[dashboard_groups_filtered["grupo_id"].isin(
            group_selected_ids)]

    if base_filtered.empty:
        st.warning(
            "Os filtros selecionados não retornaram dados para esta revisão.")
        return

    if dashboard_groups_filtered is None or dashboard_groups_filtered.empty:
        overall = overall_from_base(base_filtered)
    else:
        overall_calc = calc_global_kpis(dashboard_groups_filtered)
        overall = {
            "pct": float(overall_calc.get("pct", 0)),
            "concl": int((base_filtered["state"] == "concluido").sum()),
            "andamento": int((base_filtered["state"] == "em_andamento").sum()),
            "pend": int((base_filtered["state"] == "pendente").sum()),
            "trav": int((base_filtered["state"] == "travado").sum()),
        }

    _fragment_kpis_globais(overall)
    st.divider()

    with st.spinner("", show_time=False):
        risco, previsao, heat, crit, tl = build_inteligencia(base_filtered)
    _fragment_previsao(previsao, risco)
    st.divider()

    tabs = [
        "🏗️ Grupos",
        "🔧 Equipamentos",
        "📋 Setores",
        "🌡️ Heatmap",
        "⚠️ Criticidade",
        "📈 Timeline"]

    def _on_tab_change() -> None:
        st.session_state["_dash_tab"] = st.session_state["_dash_tab_ctrl"]

    active = st.session_state.get("_dash_tab", tabs[0])
    if active not in tabs:
        active = tabs[0]

    st.segmented_control(
        "Visão",
        tabs,
        default=active,
        key="_dash_tab_ctrl",
        on_change=_on_tab_change,
        label_visibility="collapsed")
    active = st.session_state.get("_dash_tab", tabs[0])

    if active == "🏗️ Grupos":
        st.markdown("### Progresso por grupo")
        _fragment_grupos(
            base_filtered,
            dept_map,
            dashboard_groups_filtered,
            top_n=top_n)
    elif active == "🔧 Equipamentos":
        st.markdown("### Progresso por equipamento")
        _fragment_equipamentos(base_filtered, dept_map, top_n=top_n)
    elif active == "📋 Setores":
        st.markdown("### Progresso por setor")
        _fragment_setores(base_filtered, top_n=top_n)
    elif active == "🌡️ Heatmap":
        st.markdown("### Heatmap de risco — Grupo × Setor")
        _fragment_heatmap(heat)
    elif active == "⚠️ Criticidade":
        st.markdown("### Top equipamentos críticos")
        _fragment_criticidade(crit)
    else:
        st.markdown("### Timeline de movimentações")
        _fragment_timeline(tl)
