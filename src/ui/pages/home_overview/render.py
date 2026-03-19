"""Home Overview — camada de renderização.

Melhorias Streamlit 1.42+:
  - st.metric nativo no lugar de _kpi_card HTML customizado
  - @st.fragment para cards de KPI e ranking em reruns parciais
  - st.status para carregamento granular
  - st.dataframe com ProgressColumn para tabelas de departamento
  - st.popover para ajuda contextual nos cards
  - st.segmented_control para troca de visão (resumo / tendência)
"""
from __future__ import annotations

import time

import pandas as pd
import plotly.express as px
import streamlit as st

from src.auth.scope import get_my_scope
from src.auth.permissions import can_view_all_data
from src.domain.kpi import calc_global_kpis, calc_dept_kpis
from src.ui.core.styles import page_header
from src.ui.components.feedback import selection_summary
from src.ui.components.actions import refresh_button, primary_action_button
from src.ui.components.tables import data_table
from src.ui.components.states import empty_message, loading_block
from src.ui.core.cache import bump_data_version
from src.utils import nav
from src.utils.kpi_engine import get_group_kpis
from src.utils.ui_helpers import status_badge, mobile_columns
from src.utils.nav import get_current_revisao, set_current_revisao
from src.utils.supabase_helpers import current_tenant_id

from .data import (
    load_revision, load_groups, load_depts,
    load_snapshots, load_group_sector_view,
    snapshots_supported, insert_snapshot,
)
from .transforms import (
    enforce_home_schema, rev_start_end, current_week,
    enrich_kdf, compute_coverage, compute_dept_summary, build_trend_chart_data,
)


# ── Fragment: KPIs principais ───────────────────────────────────────────

@st.fragment
def _fragment_kpis(
    kdf: pd.DataFrame,
    dep_total: int,
    dep_done: int,
    cov: dict,
    gk: dict,
) -> None:
    """KPIs com st.metric nativo — reroda independentemente dos tabs."""
    # mobile: stack KPIs em 1 coluna
    _kpi2 = mobile_columns(2, 1)
    r1c1, r1c2 = (_kpi2 * 2)[:2]
    with r1c1:
        st.metric(
            "% concluído",
            f"{gk['pct']}%",
            delta=f"Etapas: {gk['done_steps']:,}/{gk['expected_steps']:,}",
            delta_color="off",
            help="Percentual global ponderado por expected_steps de cada grupo.",
        )
    with r1c2:
        st.metric(
            "Departamentos concluídos",
            f"{dep_done}/{dep_total}",
            delta=f"Frotas: {cov['eq_done']}/{cov['eq_total']}",
            delta_color="off",
        )

    _kpi2b = mobile_columns(2, 1)
    r2c1, r2c2 = (_kpi2b * 2)[:2]
    with r2c1:
        st.metric(
            "Risco",
            f"{cov['risco_pct']}%",
            delta="grupos < 50%" if cov["risco_pct"] > 0 else "sem grupos críticos",
            delta_color="inverse" if cov["risco_pct"] > 0 else "off",
            help="Proporção de grupos com equipamentos + template e % de execução < 50.",
        )
    with r2c2:
        st.metric(
            "Cobertura",
            f"{cov['grupos_com_peso']}/{cov['total_grupos']}",
            delta="c/ equipamentos + template",
            delta_color="off",
            help="Grupos que têm equipamentos ativos E template de serviços configurado.",
        )

    # Popover de alerta de cobertura (não ocupa espaço vertical permanente)
    if cov["grupos_com_peso"] <= 1 and cov["total_grupos"] >= 2:
        with st.popover("⚠️ Cobertura baixa — saiba mais"):
            st.warning(
                f"Apenas **{cov['grupos_com_peso']}/{cov['total_grupos']}** grupos "
                "têm equipamentos ativos + template. O % global pode refletir apenas um grupo."
            )
            if st.session_state.get("current_role") in ("admin", "superadmin"):
                if st.button(
                    "Abrir Templates",
                    use_container_width=True,
                        key="home_go_templates_pop"):
                    nav.goto("Templates")


# ── Fragment: ranking de grupos ─────────────────────────────────────────

@st.fragment
def _fragment_ranking(
    scope: pd.DataFrame,
    tenant_id: str,
    revisao_id: str,
    ver: str,
) -> None:
    """Top 5 melhores / críticos com detalhe de setores."""
    if scope.empty:
        st.info("Sem grupos configurados (equipamentos + template) para ranquear.")
        return

    best = scope.sort_values(["pct", "done_steps"],
                             ascending=[False, False]).head(5)
    worst = scope.sort_values(["pct", "done_steps"],
                              ascending=[True, True]).head(5)

    a, b = st.columns(2)
    sector_map = load_group_sector_view(tenant_id, revisao_id, ver)

    def _render_grupo_card(row: dict, mode: str) -> None:
        gid = row.get("grupo_id")
        sec = sector_map.get(str(gid), {})
        pct = int(pd.to_numeric(row.get("pct", 0), errors="coerce") or 0)
        total = int(sec.get("setores_total") or 0)
        concl = int(sec.get("setores_concluidos") or 0)
        pend = int(sec.get("setores_pendentes") or 0)

        with st.container(border=True):
            col_l, col_r = st.columns([0.75, 0.25])
            with col_l:
                st.markdown(f"**{row.get('Grupo', 'Grupo')}**")
            with col_r:
                status_badge("concluido" if mode == "best" else "travado")

            mc1, mc2, mc3 = st.columns(3)
            with mc1:
                st.metric("Execução", f"{pct}%")
            with mc2:
                st.metric(
                    "Setores pend.",
                    pend,
                    delta_color="inverse" if pend > 0 else "off")
            with mc3:
                st.metric("Setores conc.", f"{concl}/{total}")
            st.caption("Setor fecha quando D+R+M ok em todos os equipamentos.")
            if st.button("Abrir na Matriz", key=f"rank_open_{mode}_{gid}",
                         use_container_width=True, type="secondary"):
                st.session_state.update({
                    "matriz_grupo_id": gid,
                    "matriz_view": "group",
                    "matriz_departamento_id": None,
                })
                nav.goto("Matriz")

    with a:
        st.markdown("### Top 5 melhores")
        for r in best.to_dict("records"):
            _render_grupo_card(r, "best")
    with b:
        st.markdown("### Top 5 críticos")
        for r in worst.to_dict("records"):
            _render_grupo_card(r, "worst")


# ── Fragment: departamentos pendentes ───────────────────────────────────

@st.fragment
def _fragment_departamentos(
    dsum: pd.DataFrame,
    dep_total: int,
    dept_to_name: dict,
    scope: pd.DataFrame,
) -> None:
    if dsum is None or getattr(dsum, "empty", True) or dep_total == 0:
        empty_message("Sem dados por departamento.")
        return

    dsum_v = dsum.copy()
    dsum_v["Departamento"] = dsum_v["departamento_id"].map(
        dept_to_name).fillna(dsum_v["departamento_id"].astype(str))
    dsum_v["Concluído"] = pd.to_numeric(
        dsum_v["pct"], errors="coerce").fillna(0) >= 100
    pend = dsum_v[~dsum_v["Concluído"]].sort_values("pct")
    done = dsum_v[dsum_v["Concluído"]].sort_values("pct", ascending=False)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### Pendentes")
        if pend.empty:
            empty_message("Nenhum departamento pendente.", kind="success")
        else:
            data_table(
                pend[["Departamento", "pct", "grupos"]].rename(columns={"pct": "%", "grupos": "Grupos"}),
                column_config={"%": st.column_config.ProgressColumn("%", min_value=0, max_value=100)},
            )
    with c2:
        st.markdown("#### Concluídos")
        if done.empty:
            empty_message("Ainda não há departamentos concluídos.")
        else:
            data_table(
                done[["Departamento", "pct", "grupos"]].rename(columns={"pct": "%", "grupos": "Grupos"}),
                column_config={"%": st.column_config.ProgressColumn("%", min_value=0, max_value=100)},
            )

    with st.expander("Detalhe por grupos e etapas", expanded=False):
        if not scope.empty:
            top_backlog = scope.sort_values(
                ["backlog_steps", "pct"], ascending=[False, True]).head(10)
            data_table(
                top_backlog[["Grupo", "pct", "done_steps", "expected_steps", "eq_count", "svc_count", "backlog_steps"]]
                .rename(columns={"pct": "%", "done_steps": "Feitas", "expected_steps": "Esperadas",
                                 "eq_count": "Equip", "svc_count": "Serviços", "backlog_steps": "Etapas pend."}),
                column_config={
                    "%": st.column_config.ProgressColumn("%", min_value=0, max_value=100),
                    "Feitas": st.column_config.NumberColumn("Feitas", format="%,d"),
                    "Esperadas": st.column_config.NumberColumn("Esperadas", format="%,d"),
                    "Etapas pend.": st.column_config.NumberColumn("Etapas pend.", format="%,d"),
                },
            )


# ── Fragment: tendência semanal ─────────────────────────────────────────

@st.fragment
def _fragment_tendencia(
    tenant_id: str,
    revisao_id: str,
    ver: str,
    week: int,
    scope: pd.DataFrame,
) -> None:
    if not snapshots_supported():
        empty_message(
            "Tabela **kpi_snapshots** não encontrada.",
            "Rode o SQL de próximos passos para habilitar tendência semanal.",
            kind="warning",
        )
        return

    if st.button("Salvar snapshot desta semana", icon=":material/save:",
                 use_container_width=True, key="home_save_snapshot"):
        ok, msg = insert_snapshot(tenant_id, revisao_id, week, scope)
        if ok:
            st.toast("✓ Snapshot salvo", icon=":material/check_circle:")
            st.cache_data.clear()
        else:
            st.error(f"Falha: {msg}")

    sdf = load_snapshots(tenant_id, revisao_id, ver)
    if sdf.empty:
        empty_message("Ainda não há snapshots salvos para esta revisão.")
        return

    g = build_trend_chart_data(sdf)
    fig_t = px.line(g, x="week_number", y="pct", markers=True, text="pct")
    fig_t.update_traces(
        texttemplate="%{text:.1f}%", textposition="top center",
        hovertemplate="Semana %{x}<br>%{y:.1f}%<extra></extra>",
    )
    fig_t.update_layout(
        height=340, margin=dict(l=12, r=12, t=10, b=10),
        paper_bgcolor="#06080B", plot_bgcolor="#0C111A",
        xaxis=dict(title="Semana", gridcolor="rgba(255,255,255,0.06)"),
        yaxis=dict(title="% concluído", range=[0, 105], gridcolor="rgba(255,255,255,0.06)"),
        font=dict(color="#E8EDF5", family="DM Sans, sans-serif", size=12),
    )
    st.plotly_chart(fig_t, use_container_width=True,
                    config={"displayModeBar": False})
    st.caption(
        "Percentual global ponderado por semana, com base nos snapshots salvos.")

    # Tabela dos snapshots com DatetimeColumn nativa
    if "created_at" in sdf.columns:
        with st.expander("Ver snapshots salvos", expanded=False):
            data_table(
                sdf.sort_values("week_number", ascending=False).head(50),
                column_config={
                    "created_at": st.column_config.DatetimeColumn(
                        "Salvo em",
                        format="DD/MM/YYYY HH:mm",
                        timezone="America/Sao_Paulo",
                    ),
                    "pct": st.column_config.ProgressColumn("% KPI", min_value=0, max_value=100),
                },
            )


# ── Fragment: risco por departamento ─────────────────────────────────────────

@st.fragment
def _fragment_risco(
        kdf: pd.DataFrame,
        gid_to_dept: dict,
        dept_to_name: dict) -> None:
    ddf = calc_dept_kpis(kdf, gid_to_dept)
    if ddf.empty:
        empty_message("Sem dados por departamento.")
        return

    ddf = ddf.copy()
    ddf["Departamento"] = ddf["departamento_id"].map(
        dept_to_name).fillna(ddf["departamento_id"].astype(str))
    ddf["Risco"] = ((100 - ddf["pct"]) * 2 + (ddf["backlog_steps"] /
                    ddf["grupos"].clip(lower=1))).round().astype(int)
    ddf = ddf.sort_values("Risco", ascending=False)

    data_table(
        ddf[["Departamento", "pct", "backlog_steps", "grupos", "Risco"]].rename(
            columns={"pct": "%", "backlog_steps": "Etapas pendentes", "grupos": "Grupos"}
        ),
        column_config={
            "%": st.column_config.ProgressColumn("%", min_value=0, max_value=100),
            "Risco": st.column_config.NumberColumn("Risco", help="Fórmula: (100-%) × 2 + backlog/grupos"),
        },
    )


# ── Ponto de entrada público ────────────────────────────────────────────

def render_home_overview() -> None:
    page_header("Home")
    tenant_id = current_tenant_id()
    if not tenant_id:
        st.info("Selecione um tenant para ver o resumo.")
        return

    ver = str(st.session_state.get("data_version", "0"))
    rev = load_revision(tenant_id, ver, get_current_revisao())
    if not rev:
        st.warning("Nenhuma revisão encontrada para este tenant.")
        return

    rev_start, _rev_end, semanas_total = rev_start_end(rev)
    week = current_week(rev_start, semanas_total)

    if rev.get("id"):
        # Compatível com versões antigas de set_current_revisao que aceitam
        # apenas o ID.
        set_current_revisao(rev["id"])
        st.session_state["_sidebar_rev_titulo"] = rev.get("titulo")
        st.session_state["_sidebar_rev_semana"] = week
    grupos = load_groups(tenant_id, ver)
    deps = load_depts(tenant_id, ver)
    gid_to_name = {g["id"]: (g.get("nome") or "—")
                   for g in grupos if g.get("id")}
    gid_to_dept = {g["id"]: g.get("departamento_id")
                   for g in grupos if g.get("id")}
    dept_to_name = {d["id"]: (d.get("nome") or "—")
                    for d in deps if d.get("id")}

    dep_scope_ids, grp_scope_ids = get_my_scope(tenant_id)
    role = st.session_state.get("current_role") or ""
    if not can_view_all_data(role) and dep_scope_ids == [] and grp_scope_ids == []:
        st.warning("Você não possui departamentos ou grupos vinculados para visualizar esta revisão.")
        return

    if dep_scope_ids is not None:
        deps = [d for d in deps if d.get("id") in dep_scope_ids]
    if grp_scope_ids is not None:
        grupos = [g for g in grupos if g.get("id") in grp_scope_ids]

    # ── Header da revisão ───────────────────────────────────────────────────
    h1_col, h2_col = st.columns([0.82, 0.18])
    with h1_col:
        st.markdown(f"## {rev.get('titulo', 'Revisão')}")
        st.caption(
            f"Semana {week}/{semanas_total}"
            + (f" • Início {rev_start.date()}" if rev_start else "")
        )
        status_badge(rev.get("status"))

        # ── Badge de prazo ───────────────────────────────────────────────────
        try:
            from src.domain.kpi import calc_prazo
            prazo = calc_prazo(
                data_inicio=rev.get("data_inicio"),
                data_fim=rev.get("data_fim"),
            )
            if prazo["status_prazo"] != "sem_prazo":
                dr = prazo["dias_restantes"]
                if dr < 0:
                    prazo_label = f"⚠️ {abs(dr)} dias em atraso"
                    prazo_color = "red"
                elif dr == 0:
                    prazo_label = "⚠️ Vence hoje"
                    prazo_color = "orange"
                elif dr <= 7:
                    prazo_label = f"⏰ {dr} dias restantes"
                    prazo_color = "orange"
                else:
                    prazo_label = f"📅 {dr} dias restantes"
                    prazo_color = "green"
                st.badge(prazo_label, color=prazo_color)
        except Exception:
            pass

    with h2_col:
        if refresh_button("home_refresh_btn", help="Atualiza KPIs, rankings e snapshots visíveis."):
            bump_data_version()
            st.session_state["home_pulse"] = True
            st.toast("Atualizado", icon=":material/refresh:")
            st.rerun()

    selection_summary(
        "Contexto da visão",
        {
            "Revisão": rev.get("titulo") or "-",
            "Status": rev.get("status") or "-",
            "Semana": f"{week}/{semanas_total}",
            "Grupos": len(grupos),
            "Departamentos": len(deps),
        },
        caption="A Home consolida a mesma revisão ativa usada nas páginas operacionais.",
    )

    # ── Carrega KPIs ────────────────────────────────────────────────────────
    with st.spinner("", show_time=False):
        kdf = get_group_kpis(tenant_id, rev["id"], ver, prefer_mv=True, _token=st.session_state.get("sb_access_token", ""))

    kdf = enforce_home_schema(kdf)
    if kdf is None or (hasattr(kdf, "empty") and kdf.empty):
        st.info("Sem KPIs nesta revisão ainda.")
        return

    kdf = enrich_kdf(
        kdf,
        gid_to_name,
        gid_to_dept,
        dep_scope_ids,
        grp_scope_ids)
    gk = calc_global_kpis(kdf)
    cov = compute_coverage(kdf)
    dsum, dep_total, dep_done = compute_dept_summary(kdf, gid_to_dept)
    scope = kdf[(kdf["eq_count"] > 0) & (kdf["svc_count"] > 0)].copy()

    # Fragment 1: KPIs
    _fragment_kpis(kdf, dep_total, dep_done, cov, gk)

    st.divider()

    # ── Tabs com on_change lazy loading ──────────────────────────────────────
    _HOME_TABS = ["📊 Resumo", "⏳ Pendentes", "⚠️ Risco", "📈 Tendência"]

    def _on_home_tab_change() -> None:
        st.session_state["_home_tab"] = st.session_state["_home_tab_ctrl"]

    active_home = st.session_state.get("_home_tab", _HOME_TABS[0])
    if active_home not in _HOME_TABS:
        active_home = _HOME_TABS[0]

    st.segmented_control(
        "Visão",
        _HOME_TABS,
        default=active_home,
        key="_home_tab_ctrl",
        on_change=_on_home_tab_change,
        label_visibility="collapsed",
    )
    active_home = st.session_state.get("_home_tab", _HOME_TABS[0])

    if active_home == "📊 Resumo":
        _fragment_ranking(scope, tenant_id, rev["id"], ver)

    elif active_home == "⏳ Pendentes":
        st.markdown("### Departamentos (visão de fim)")
        _fragment_departamentos(dsum, dep_total, dept_to_name, scope)

    elif active_home == "⚠️ Risco":
        st.markdown("### Risco por departamento")
        _fragment_risco(kdf, gid_to_dept, dept_to_name)

    else:  # 📈 Tendência
        st.markdown("### Tendência semanal")
        _fragment_tendencia(tenant_id, rev["id"], ver, week, scope)
