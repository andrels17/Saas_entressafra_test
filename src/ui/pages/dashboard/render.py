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
from src.auth.permissions import can_view_all_data
from src.domain.kpi import calc_global_kpis, calc_dept_kpis
from src.ui.core.empty_state import empty_state
from src.ui.components.filters import multiselect_departamentos, multiselect_grupos
from src.ui.components.feedback import notice_card, selection_summary
from src.ui.components.actions import refresh_button
from src.ui.components.tables import data_table
from src.ui.components.states import empty_message, loading_block
from src.ui.core.styles import page_header
from src.ui.core.cache import bump_data_version
from src.utils.kpi_engine import get_group_kpis
from src.utils.nav import set_current_revisao
from src.utils.supabase_helpers import current_tenant_id, sb_for_user
from src.db.supabase_client import get_supabase_anon
from src.utils.observability import log_error


def _sb_from_token(token: str = ""):
    sb = get_supabase_anon()
    if token:
        sb.postgrest.auth(token)
    return sb

from src.utils.ui_helpers import status_badge

from .transforms import (
    apply_filters,
    build_inteligencia,
    equipment_progress,
    fmt_date,
    group_progress,
    normalize_matriz_base,
    overall_from_base,
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
def _load_base_cached(tenant_id: str, revisao_id: str, _token: str = "",
                      ver: str = "0") -> tuple[list, list]:
    sb = _sb_from_token(_token)

    def _fetch_all(query, page_size: int = 1000):
        rows = []
        start = 0
        while True:
            chunk = query.range(start, start + page_size - 1).execute().data or []
            rows.extend(chunk)
            if len(chunk) < page_size:
                break
            start += page_size
        return rows

    try:
        task_rows = _fetch_all(
            sb.table("tarefas_servico")
            .select("equipamento_id,servico_id,status,etapa_d,etapa_r,etapa_m,updated_at")
            .eq("tenant_id", tenant_id)
            .eq("revisao_id", revisao_id)
        )
    except Exception as exc:
        log_error(exc, context="dashboard._load_base_cached", table="tarefas_servico")
        task_rows = []

    try:
        eq_rows = _fetch_all(
            sb.table("equipamentos")
            .select("id,frota,modelo,departamento_id,grupo_id")
            .eq("tenant_id", tenant_id)
            .eq("ativo", True)
        )
    except Exception as exc:
        log_error(exc, context="dashboard._load_base_cached", table="equipamentos")
        eq_rows = []

    try:
        grupo_rows = _fetch_all(
            sb.table("equip_grupos")
            .select("id,nome,departamento_id")
            .eq("tenant_id", tenant_id)
            .eq("ativo", True)
        )
    except Exception as exc:
        log_error(exc, context="dashboard._load_base_cached", table="equip_grupos")
        grupo_rows = []

    serv_rows = []
    try:
        serv_rows = _fetch_all(
            sb.table("servicos")
            .select("id,nome,setor")
            .eq("tenant_id", tenant_id)
        )
    except Exception as exc:
        log_error(exc, context="dashboard._load_base_cached", table="servicos")

    eq_map = {str(r.get("id")): r for r in eq_rows if r.get("id") is not None}
    grupo_map = {str(r.get("id")): r for r in grupo_rows if r.get("id") is not None}
    serv_map = {str(r.get("id")): r for r in serv_rows if r.get("id") is not None}

    raw = []
    for t in task_rows:
        eid = str(t.get("equipamento_id")) if t.get("equipamento_id") is not None else None
        sid = str(t.get("servico_id")) if t.get("servico_id") is not None else None
        eq = eq_map.get(eid, {})
        gid = eq.get("grupo_id")
        gid_s = str(gid) if gid is not None else None
        grp = grupo_map.get(gid_s, {})
        svc = serv_map.get(sid, {})
        raw.append({
            "equipamento_id": t.get("equipamento_id"),
            "grupo_id": gid,
            "grupo_nome": grp.get("nome"),
            "departamento_id": eq.get("departamento_id") or grp.get("departamento_id"),
            "frota": eq.get("frota"),
            "modelo": eq.get("modelo"),
            "servico_id": t.get("servico_id"),
            "setor_nome": svc.get("setor") or "—",
            "status": t.get("status"),
            "etapa_d": t.get("etapa_d"),
            "etapa_r": t.get("etapa_r"),
            "etapa_m": t.get("etapa_m"),
            "updated_at": t.get("updated_at"),
        })

    eq_meta = [
        {
            "equipamento_id": r.get("id"),
            "frota": r.get("frota"),
            "modelo": r.get("modelo"),
            "departamento_id": r.get("departamento_id"),
        }
        for r in eq_rows
    ]
    return raw, eq_meta


@st.cache_data(ttl=120, show_spinner=False)
def _load_departamentos(tenant_id: str, ver: str = "0", _token: str = "") -> list[dict]:
    sb = _sb_from_token(_token)
    try:
        return (
            sb.table("departamentos")
            .select("id,nome")
            .eq("tenant_id", tenant_id)
            .eq("ativo", True)
            .order("nome")
            .execute()
            .data or []
        )
    except Exception as exc:
        log_error(exc, context="dashboard._load_departamentos", table="departamentos")
        return []


@st.cache_data(ttl=120, show_spinner=False)
def _load_grupos(tenant_id: str, ver: str = "0", _token: str = "") -> list[dict]:
    sb = _sb_from_token(_token)
    try:
        return (
            sb.table("equip_grupos")
            .select("id,nome,departamento_id")
            .eq("tenant_id", tenant_id)
            .eq("ativo", True)
            .order("nome")
            .execute()
            .data or []
        )
    except Exception as exc:
        log_error(exc, context="dashboard._load_grupos", table="equip_grupos")
        return []


def _pct_bar_color(v: float) -> str:
    """Cor condicional padrão: verde >= 80 | amarelo >= 50 | vermelho < 50."""
    if v >= 80:
        return "#12B76A"
    if v >= 50:
        return "#F59E0B"
    return "#EF4444"


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
        chart_df[value_col], errors="coerce").fillna(0).clip(0, 100)
    chart_df = chart_df.sort_values(value_col, ascending=False).head(top_n)
    chart_df = chart_df.sort_values(value_col, ascending=True)
    chart_df["label"] = chart_df[value_col].map(lambda v: f"{v:.1f}%")
    chart_df["color"] = chart_df[value_col].apply(_pct_bar_color)

    fig = px.bar(
        chart_df,
        x=value_col,
        y=category_col,
        orientation="h",
        text="label",
        color="color",
        color_discrete_map="identity",
        title=title,
    )
    fig.update_traces(
        textposition="outside",
        cliponaxis=False,
        hovertemplate="%{y}<br>% Concluído: %{x:.1f}%<extra></extra>")
    fig.update_layout(
        height=max(380, 42 * len(chart_df) + 80),
        margin=dict(l=10, r=90, t=48, b=10),
        xaxis=dict(range=[0, 110], title="% Concluído"),
        yaxis=dict(title="", type="category"),
        paper_bgcolor="#06080B",
        plot_bgcolor="#0C111A",
        font=dict(color="#E8EDF5", family="DM Sans, sans-serif", size=11),
        showlegend=False,
    )
    st.plotly_chart(
        fig, use_container_width=True, config={"displayModeBar": False})


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
        "no_prazo": "No prazo",
        "atraso": "Com atraso",
        "sem_base": "Sem base"}
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
    data_table(
        display.head(top_n),
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
def _fragment_departamentos(
        group_kpis_df: pd.DataFrame,
        gid_to_dept: dict,
        dept_map: dict) -> None:
    """Gráfico de barras horizontais: progresso por departamento.

    Clique em uma barra para filtrar o dashboard por aquele departamento.
    Usa on_select do Plotly — zero queries extras, apenas session_state + rerun.
    """
    if group_kpis_df is None or group_kpis_df.empty:
        empty_message("Sem dados de departamentos.")
        return

    dsum = calc_dept_kpis(group_kpis_df, gid_to_dept)
    if dsum is None or dsum.empty:
        empty_message("Sem dados de departamentos para esta revisão.")
        return

    dsum = dsum.copy()
    dsum["Departamento"] = dsum["departamento_id"].map(dept_map).fillna("—")
    dsum["pct"] = pd.to_numeric(dsum.get("pct", 0), errors="coerce").fillna(0).clip(0, 100)
    dsum["Grupos"] = dsum.get("grupos", pd.Series(dtype=int)).fillna(0).astype(int)
    dsum["Etapas feitas"] = dsum.get("done_steps", pd.Series(dtype=int)).fillna(0).astype(int)
    dsum["Etapas esperadas"] = dsum.get("expected_steps", pd.Series(dtype=int)).fillna(0).astype(int)

    display = (
        dsum[["Departamento", "pct", "Grupos", "Etapas feitas", "Etapas esperadas"]]
        .rename(columns={"pct": "% Concluído"})
        .sort_values(["% Concluído", "Departamento"], ascending=[False, True])
    )

    chart_df = display[["Departamento", "% Concluído"]].copy()
    chart_df = chart_df.sort_values("% Concluído", ascending=True)
    chart_df["label"] = chart_df["% Concluído"].map(lambda v: f"{v:.1f}%")
    chart_df["color"] = chart_df["% Concluído"].apply(_pct_bar_color)

    fig = px.bar(
        chart_df,
        x="% Concluído",
        y="Departamento",
        orientation="h",
        text="label",
        color="color",
        color_discrete_map="identity",
        title="Progresso por departamento — clique numa barra para filtrar",
    )
    fig.update_traces(
        textposition="outside",
        cliponaxis=False,
        hovertemplate="%{y}<br>% Concluído: %{x:.1f}%<br><i>Clique para filtrar</i><extra></extra>",
    )
    fig.update_layout(
        height=max(320, 52 * len(chart_df) + 80),
        margin=dict(l=10, r=90, t=52, b=10),
        xaxis=dict(range=[0, 110], title="% Concluído"),
        yaxis=dict(title="", type="category"),
        paper_bgcolor="#06080B",
        plot_bgcolor="#0C111A",
        font=dict(color="#E8EDF5", family="DM Sans, sans-serif", size=12),
        showlegend=False,
    )

    # on_select: Streamlit captura o clique sem rerenderizar nada — zero custo
    event = st.plotly_chart(
        fig,
        use_container_width=True,
        config={"displayModeBar": False},
        on_select="rerun",
        key="dash_dept_chart",
    )

    # Processa clique: aplica filtro e reexecuta sem nenhuma query extra
    # session_state["dash_filter_dept"] armazena NOMES (o que st.multiselect usa internamente)
    if event and event.get("selection", {}).get("points"):
        clicked_name = event["selection"]["points"][0].get("y", "")
        if clicked_name and clicked_name != "—":
            current = st.session_state.get("dash_filter_dept", [])
            if clicked_name in current:
                # segundo clique no mesmo: remove (toggle)
                new_val = [x for x in current if x != clicked_name]
            else:
                new_val = [clicked_name]
            st.session_state["dash_filter_dept"] = new_val
            st.rerun()

    st.caption("💡 Clique em uma barra para filtrar o dashboard por departamento. Clique novamente para limpar.")

    data_table(
        display,
        column_config={
            "% Concluído": st.column_config.ProgressColumn(
                "% Concluído", min_value=0, max_value=100, format="%.1f%%"),
            "Grupos": st.column_config.NumberColumn("Grupos", format="%d"),
            "Etapas feitas": st.column_config.NumberColumn("Etapas feitas", format="%d"),
            "Etapas esperadas": st.column_config.NumberColumn("Etapas esperadas", format="%d"),
        },
    )

    with st.expander("⬇ Exportar", expanded=False):
        from src.utils.ui_helpers import df_to_xlsx
        col_csv, col_xlsx = st.columns(2)
        with col_csv:
            st.download_button(
                "CSV", display.to_csv(index=False).encode("utf-8"),
                file_name="dashboard_departamentos.csv", mime="text/csv",
                use_container_width=True, key="dash_dept_csv",
            )
        with col_xlsx:
            st.download_button(
                "XLSX",
                df_to_xlsx(display, sheet_name="Departamentos"),
                file_name="dashboard_departamentos.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True, key="dash_dept_xlsx",
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
    data_table(
        rank_df[cols],
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
        empty_message("Sem dados de heatmap para esta revisão.")
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
        empty_message("Sem equipamentos críticos para exibir.")
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
    data_table(
        crit[present].head(20),
        column_config={
            "ranking_criticidade": st.column_config.NumberColumn("#", width="small"),
            "criticidade_score": st.column_config.NumberColumn("Score", format="%.2f"),
            "pct_concluido": st.column_config.ProgressColumn("% Concluído", min_value=0, max_value=100),
        },
    )


@st.fragment
def _fragment_timeline(tl: pd.DataFrame) -> None:
    if tl.empty:
        empty_message("Sem movimentações registradas nesta revisão.")
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
    data_table(
        tl.sort_values("dia", ascending=False),
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
    dep_scope_ids, grp_scope_ids = get_my_scope(tenant_id, sb)
    role = st.session_state.get("current_role") or ""
    if not can_view_all_data(role) and dep_scope_ids == [] and grp_scope_ids == []:
        st.warning("Você não possui departamentos ou grupos vinculados para visualizar o dashboard.")
        return

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
        if refresh_button("dash_refresh_btn", help="Recarrega os dados consolidados desta revisão."):
            bump_data_version()
            st.toast("Atualizado", icon=":material/refresh:")
            st.rerun()

    selection_summary(
        "Contexto da revisão",
        {
            "Revisão": rev.get("titulo") or "Revisão ativa",
            "Status": rev.get("status") or "-",
            "Período": f"{rev.get('data_inicio') or '-'} -> {rev.get('data_fim') or '-'}",
        },
        caption="Os filtros abaixo refinam apenas a visualização atual do dashboard.",
    )

    ver = str(st.session_state.get("data_version", "0"))
    with st.spinner("", show_time=False):
        raw, eq_meta = _load_base(sb, tenant_id, revisao_id)
        departamentos = _load_departamentos(tenant_id, ver, st.session_state.get("sb_access_token", ""))
        grupos = _load_grupos(tenant_id, ver, st.session_state.get("sb_access_token", ""))

    if dep_scope_ids in (None, [] ) and grp_scope_ids not in (None, []):
        dep_scope_ids = sorted({str(g.get("departamento_id")) for g in grupos if g.get("id") in set(grp_scope_ids) and g.get("departamento_id")})

    if dep_scope_ids == [] and grp_scope_ids not in (None, []):
        grp_set = {str(x) for x in grp_scope_ids}
        departamentos = [d for d in departamentos if any(str(g.get("id")) in grp_set and str(g.get("departamento_id")) == str(d.get("id")) for g in grupos)]

    if dep_scope_ids is not None:
        dep_scope_set = {str(x) for x in dep_scope_ids}
        departamentos = [d for d in departamentos if str(d.get("id")) in dep_scope_set]
    if grp_scope_ids is not None:
        grp_scope_set = {str(x) for x in grp_scope_ids}
        scoped_grupos = [g for g in grupos if str(g.get("id")) in grp_scope_set]
        if scoped_grupos or dep_scope_ids in (None, []):
            grupos = scoped_grupos
        else:
            grupos = [g for g in grupos if g.get("departamento_id") in dep_scope_ids]

    dept_map = {d["id"]: d.get("nome", "—")
                for d in departamentos if d.get("id")}
    gid_to_name = {g["id"]: g.get("nome", "—") for g in grupos if g.get("id")}
    gid_to_dept = {g["id"]: g.get("departamento_id")
                   for g in grupos if g.get("id")}

    base = normalize_matriz_base(raw, eq_meta)
    if base.empty:
        notice_card(
            "Sem dados de execução",
            "A revisão foi encontrada, mas ainda não há tarefas suficientes para consolidar o dashboard desta revisão.",
            tone="warning",
        )
        return

    base = apply_filters(base, dep_scope_ids, grp_scope_ids)
    if base.empty and dep_scope_ids not in (None, []):
        # fallback defensivo: quando o vínculo vier por departamento e a lista de grupos
        # estiver desatualizada, mantém o filtro por departamento em vez de zerar o dashboard.
        base = apply_filters(base=normalize_matriz_base(raw, eq_meta), departamento_ids=dep_scope_ids, grupo_ids=None)

    prefer_mv = str(rev.get("status") or "").lower() in ("concluida", "encerrada", "fechada")
    group_kpis_df = get_group_kpis(tenant_id, revisao_id, ver, prefer_mv=prefer_mv, _token=st.session_state.get("sb_access_token", ""))
    if group_kpis_df is not None and not group_kpis_df.empty:
        if grp_scope_ids not in (None, []):
            scoped_group_kpis = group_kpis_df[group_kpis_df["grupo_id"].isin(grp_scope_ids)]
            if not scoped_group_kpis.empty or dep_scope_ids in (None, []):
                group_kpis_df = scoped_group_kpis
        if dep_scope_ids is not None:
            group_kpis_df = group_kpis_df[group_kpis_df["grupo_id"].map(gid_to_dept).isin(dep_scope_ids)]
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

    # Sem seleção manual, o dashboard deve usar automaticamente todo o escopo disponível.
    all_visible_dept_ids = [str(d.get("id")) for d in departamentos if d.get("id")]
    all_visible_group_ids = [str(g.get("id")) for g in grupos if g.get("id")]

    effective_dept_ids = dept_selected_ids or all_visible_dept_ids
    if group_selected_ids:
        effective_group_ids = group_selected_ids
    else:
        if dept_selected_ids:
            dept_set = {str(x) for x in dept_selected_ids}
            effective_group_ids = [
                str(g.get("id"))
                for g in grupos
                if g.get("id") and str(g.get("departamento_id")) in dept_set
            ]
        else:
            effective_group_ids = all_visible_group_ids

    selection_summary(
        "Filtro aplicado",
        {
            "Departamentos": len(dept_selected_ids) or "Todos",
            "Grupos": len(group_selected_ids) or "Todos",
            "Ranking": f"Top {top_n}",
        },
    )

    base_filtered = apply_filters(base, effective_dept_ids, effective_group_ids)
    dashboard_groups_filtered = dashboard_groups.copy()
    if effective_dept_ids and "departamento_id" in dashboard_groups_filtered.columns:
        dashboard_groups_filtered = dashboard_groups_filtered[dashboard_groups_filtered["departamento_id"].isin(
            effective_dept_ids)]
    if effective_group_ids and "grupo_id" in dashboard_groups_filtered.columns:
        dashboard_groups_filtered = dashboard_groups_filtered[dashboard_groups_filtered["grupo_id"].isin(
            effective_group_ids)]

    if base_filtered.empty:
        notice_card(
            "Nenhum resultado para os filtros",
            "A combinação de departamentos e grupos não retornou dados nesta revisão. Ajuste os filtros para continuar.",
            tone="warning",
        )
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
        "Departamentos",
        "Grupos",
        "Equipamentos",
        "Heatmap",
        "Criticidade",
        "Timeline"]

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

    if active == "Grupos":
        st.markdown("### Progresso por grupo")
        _fragment_grupos(
            base_filtered,
            dept_map,
            dashboard_groups_filtered,
            top_n=top_n)
    elif active == "Departamentos":
        st.markdown("### Progresso por departamento")
        _fragment_departamentos(dashboard_groups_filtered, gid_to_dept, dept_map)
    elif active == "Equipamentos":
        st.markdown("### Progresso por equipamento")
        _fragment_equipamentos(base_filtered, dept_map, top_n=top_n)
    elif active == "Heatmap":
        st.markdown("### Heatmap de risco — Grupo × Setor")
        _fragment_heatmap(heat)
    elif active == "Criticidade":
        st.markdown("### Top equipamentos críticos")
        _fragment_criticidade(crit)
    else:
        st.markdown("### Timeline de movimentações")
        _fragment_timeline(tl)
