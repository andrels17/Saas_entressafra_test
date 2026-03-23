"""Renderização principal da Matriz Operacional (módulo modularizado)."""
from __future__ import annotations

import json
from copy import deepcopy

import altair as alt
import pandas as pd
import streamlit as st

from src.ui.core.styles import page_header as _ph
from src.ui.pages.matriz_runtime import sector_set_open as _sector_set_open
from src.utils.timezone import now_utc as _now_utc

from .context import build_group_context, handle_toolbar_reload, load_matrix_base_context
from .header import render_group_header
from .insights import (
    _build_automation_insights,
    _build_group_sector_intelligence,
    _fmt_duration_from_hours,
    _sector_priority_sort_key,
)
from .selection import render_selection_screen
from .styles import _inject_css
from .summary_tab import render_summary_tab


@st.cache_data(ttl=60, show_spinner=False)
def _build_evo_chart_data(
    tarefas_json: str,
    svc_ids_rank: tuple[str, ...],
    total_cells_rank: int,
    semanas_total: int,
    rev_start_iso: str,
) -> dict | None:
    tarefas = json.loads(tarefas_json)
    df = pd.DataFrame(tarefas)
    if df.empty:
        return None

    rev_start = pd.to_datetime(rev_start_iso, utc=True)
    svc_set = set(svc_ids_rank)

    def _wk(s):
        dt = pd.to_datetime(s, errors="coerce", utc=True)
        return ((dt - rev_start).dt.days.clip(lower=0) // 7 + 1).astype("Int64")

    has_dt = any(c in df.columns and df[c].notna().any() for c in ["dt_etapa_d", "dt_etapa_r", "dt_etapa_m"])
    if has_dt:
        events = []
        for dc in ["dt_etapa_d", "dt_etapa_r", "dt_etapa_m"]:
            if dc not in df.columns:
                continue
            sub = df[df["servico_id"].isin(svc_set)].copy()
            if sub.empty:
                continue
            sub["wk"] = _wk(sub[dc])
            sub = sub.dropna(subset=["wk"])
            if not sub.empty:
                events.append(sub[["wk"]].assign(cnt=1))
        if not events:
            return {"mode": "no_timestamps"}

        ev = pd.concat(events, ignore_index=True)
        agg = ev.groupby("wk", dropna=True)["cnt"].sum().sort_index()
        cum = agg.cumsum()
        mw = int(max(cum.index.max(), agg.index.max()))
        idx = range(1, mw + 1)
        wt = max(semanas_total, mw, 1)

        pc = (cum / max(total_cells_rank, 1) * 100).round(1).to_frame("Cumulativo (%)")
        ps = (agg / max(total_cells_rank, 1) * 100).round(1).to_frame("Na semana (%)")
        pc = pc.reindex(idx).ffill().fillna(0)
        ps = ps.reindex(idx).fillna(0)
        meta = pd.Series([min(100.0, (w / wt) * 100) for w in idx], index=idx, name="Meta (%)")
        return {"mode": "timestamps", "pc": pc, "ps": ps, "meta": meta, "agg": agg, "cum": cum}

    if "semana" in df.columns:
        df_done = df[df["servico_id"].isin(svc_set) & df["semana"].notna()].copy()
        if df_done.empty:
            return None
        df_done["semana"] = pd.to_numeric(df_done["semana"], errors="coerce").astype("Int64")
        df_done = df_done.dropna(subset=["semana"])
        cum_vals = []
        for w in sorted(df_done["semana"].unique()):
            w_df = df_done[df_done["semana"] <= w]
            ok_w = int(w_df[["etapa_d", "etapa_r", "etapa_m"]].fillna(False).astype(bool).astype(int).sum().sum())
            cum_vals.append({"Semana": int(w), "% Concluído": round((ok_w / max(total_cells_rank, 1)) * 100, 1)})
        return {"mode": "semana_col", "cum_vals": cum_vals}

    return None


@st.cache_data(ttl=90, show_spinner=False)
def _build_cached_analytics(
    *,
    eqs_json: str,
    setor_to_services_json: str,
    task_map_json: str,
    atraso_dias_json: str,
    rev_start_iso: str,
    pct_geral: float,
    resumo_json: str,
    semanas_total: int,
    now_ref_iso: str,
) -> dict:
    eqs = json.loads(eqs_json)
    setor_to_services = json.loads(setor_to_services_json)
    task_map_raw = json.loads(task_map_json)
    atraso_dias = json.loads(atraso_dias_json)
    resumo_rows = json.loads(resumo_json)

    task_map = {}
    for key, value in task_map_raw.items():
        if "||" in key:
            eid, sid = key.split("||", 1)
            task_map[(eid, sid)] = value

    analytics_sector_intelligence = _build_group_sector_intelligence(
        equipamentos=eqs,
        setor_to_services=setor_to_services,
        task_map=task_map,
        atraso_dias=atraso_dias,
        rev_start=pd.to_datetime(rev_start_iso, utc=True),
    )
    analytics_priority_sorted = sorted(analytics_sector_intelligence, key=_sector_priority_sort_key)

    try:
        elapsed_days = max(
            0,
            int((pd.Timestamp(now_ref_iso, tz="UTC") - pd.to_datetime(rev_start_iso, utc=True)).days),
        )
    except Exception:
        elapsed_days = 0

    current_week_no = int(elapsed_days // 7 + 1)
    total_weeks_plan = int(semanas_total or current_week_no or 1)
    expected_pct_now = round(min(100.0, (current_week_no / max(total_weeks_plan, 1)) * 100), 1)
    progresso_atual_pct = float(pct_geral)

    resumo_df = pd.DataFrame(resumo_rows)
    critical_eq_count = int((resumo_df["%"] < 50).sum()) if not resumo_df.empty and "%" in resumo_df.columns else 0
    no_start_eq_count = int((resumo_df["%"] == 0).sum()) if not resumo_df.empty and "%" in resumo_df.columns else 0

    automation_insights = _build_automation_insights(
        sector_intelligence=analytics_sector_intelligence,
        progresso_atual=progresso_atual_pct,
        meta_atual=expected_pct_now,
        critical_eq_count=critical_eq_count,
        no_start_eq_count=no_start_eq_count,
    )

    return {
        "analytics_sector_intelligence": analytics_sector_intelligence,
        "analytics_priority_sorted": analytics_priority_sorted,
        "expected_pct_now": expected_pct_now,
        "progresso_atual_pct": progresso_atual_pct,
        "delta_vs_expected_now": round(progresso_atual_pct - expected_pct_now, 1),
        "critical_eq_count": critical_eq_count,
        "automation_insights": automation_insights,
        "current_week_no": current_week_no,
        "total_weeks_plan": total_weeks_plan,
    }


def _serialize_task_map(task_map) -> str:
    payload = {f"{eid}||{sid}": value for (eid, sid), value in task_map.items()}
    return json.dumps(payload, default=str, sort_keys=True)


def _group_cache_store() -> dict:
    return st.session_state.setdefault("_mtz_group_ctx_cache", {})


def _resumo_cache_store() -> dict:
    return st.session_state.setdefault("_mtz_resumo_cache", {})


def _guess_selected_group_id() -> str:
    for key in (
        "matriz_grupo_id",
        "matriz_selected_grupo_id",
        "grupo_id",
        "selected_group_id",
        "selected_grupo_id",
    ):
        value = st.session_state.get(key)
        if value:
            return str(value)
    return ""


def _group_signature(base_ctx) -> tuple[str, str, str, str]:
    tenant_id = str(getattr(base_ctx, "tenant_id", "") or "")
    revisao_id = str(st.session_state.get("matriz_revisao_id") or "")
    group_id = _guess_selected_group_id()
    data_version = str(st.session_state.get("data_version", "0"))
    return tenant_id, revisao_id, group_id, data_version


def _purge_stale_group_cache(active_sig: tuple[str, str, str, str]) -> None:
    store = _group_cache_store()
    stale_keys = [k for k in store if k != active_sig]
    for key in stale_keys:
        store.pop(key, None)

    resumo_store = _resumo_cache_store()
    stale_resumo = [k for k in resumo_store if k != active_sig]
    for key in stale_resumo:
        resumo_store.pop(key, None)


def _get_cached_group_context(base_ctx):
    sig_before = _group_signature(base_ctx)
    store = _group_cache_store()
    cached = store.get(sig_before)
    if cached is not None:
        return deepcopy(cached)

    group_ctx = build_group_context(base_ctx)
    if not group_ctx:
        return None

    sig_after = (
        str(getattr(base_ctx, "tenant_id", "") or ""),
        str(getattr(group_ctx, "revisao_id", "") or st.session_state.get("matriz_revisao_id") or ""),
        str(getattr(group_ctx, "grupo_id", "") or _guess_selected_group_id()),
        str(st.session_state.get("data_version", "0")),
    )
    _purge_stale_group_cache(sig_after)
    store[sig_after] = deepcopy(group_ctx)
    return group_ctx


def _get_cached_resumo_json(group_ctx) -> str:
    sig = (
        "",
        str(getattr(group_ctx, "revisao_id", "") or ""),
        str(getattr(group_ctx, "grupo_id", "") or ""),
        str(st.session_state.get("data_version", "0")),
    )
    store = _resumo_cache_store()
    cached = store.get(sig)
    if cached is not None:
        return cached

    resumo_json = group_ctx.resumo_df.to_json(orient="records", date_format="iso") if not group_ctx.resumo_df.empty else "[]"
    store[sig] = resumo_json
    return resumo_json


def _invalidate_matrix_perf_cache() -> None:
    _group_cache_store().clear()
    _resumo_cache_store().clear()
    for fn in (_build_evo_chart_data, _build_cached_analytics):
        try:
            fn.clear()
        except Exception:
            pass


def _render_altair_evo(pc: pd.DataFrame, ps: pd.DataFrame, meta: pd.Series) -> None:
    df_chart = pc.join(ps).join(meta).reset_index(names="Semana")
    df_melt = df_chart.melt("Semana", var_name="série", value_name="valor")
    df_meta = df_chart[["Semana", "Meta (%)"]].rename(columns={"Meta (%)": "meta_val"})
    df_melt = df_melt.merge(df_meta, on="Semana", how="left")
    df_melt["delta"] = (df_melt["valor"] - df_melt["meta_val"]).round(1)

    nearest = alt.selection_point(nearest=True, on="mouseover", fields=["Semana"], empty=False)
    color_scale = alt.Scale(
        domain=["Cumulativo (%)", "Meta (%)", "Na semana (%)"],
        range=["#7F77DD", "#378ADD", "#D85A30"],
    )

    base = alt.Chart(df_melt).encode(
        x=alt.X("Semana:Q", axis=alt.Axis(tickMinStep=1, title="Semana")),
        y=alt.Y("valor:Q", axis=alt.Axis(title="%", format=".1f"), scale=alt.Scale(domain=[0, 100])),
        color=alt.Color("série:N", scale=color_scale, legend=alt.Legend(orient="bottom", title=None)),
    )

    lines = base.mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=40, filled=True))
    points_highlight = (
        base.mark_point(size=80, filled=True, opacity=0)
        .encode(
            opacity=alt.condition(nearest, alt.value(1), alt.value(0)),
            tooltip=[
                alt.Tooltip("Semana:Q", title="Semana"),
                alt.Tooltip("série:N", title="Série"),
                alt.Tooltip("valor:Q", title="%", format=".1f"),
                alt.Tooltip("delta:Q", title="vs meta", format="+.1f"),
            ],
        )
        .add_params(nearest)
    )

    rule = (
        alt.Chart(df_melt)
        .mark_rule(color="#888780", strokeWidth=1, strokeDash=[4, 3], opacity=0.6)
        .encode(x="Semana:Q")
        .transform_filter(nearest)
    )
    chart = (
        (lines + points_highlight + rule)
        .properties(height=320)
        .configure_view(strokeWidth=0)
        .configure_axis(gridOpacity=0.15, domainOpacity=0.3)
        .configure_legend(labelFontSize=12, symbolSize=80)
    )
    st.altair_chart(chart, use_container_width=True)


def _render_toolbar(base_ctx):
    hph = st.empty()
    with hph.container():
        st.markdown('<div class="enterprise-sticky">', unsafe_allow_html=True)
        st.markdown('<div class="enterprise-title">Matriz Operacional</div>', unsafe_allow_html=True)
        st.markdown('<div class="enterprise-sub">Filtros, revisão e acesso rápido aos grupos</div>', unsafe_allow_html=True)

        clear_dept = False
        show_all = False
        reload_data = False

        row1_c1, row1_c2, row1_c3 = st.columns([1.7, 1.1, 0.7], vertical_alignment="bottom")
        with row1_c1:
            st.session_state.setdefault("matriz_grp_search", "")
            search = st.text_input(
                "Buscar",
                value=st.session_state["matriz_grp_search"],
                placeholder="Grupo ou departamento…",
                key="mtz_search_in",
            )
            st.session_state["matriz_grp_search"] = search
        with row1_c2:
            rev_opts = [(r.get("titulo") or f"Revisao {r['id']}", r["id"]) for r in base_ctx.revisoes if r.get("id")]
            if not rev_opts:
                st.selectbox("Revisao", ["Nenhuma revisao"], disabled=True, key="rev_pick_dis")
            else:
                labels = [lbl for lbl, _ in rev_opts]
                mapping = {lbl: rid for lbl, rid in rev_opts}
                cur = next((lbl for lbl, rid in rev_opts if rid == st.session_state["matriz_revisao_id"]), labels[0])
                pick = st.selectbox("Revisao", labels, index=labels.index(cur), key="mtz_rev_pick")
                st.session_state["matriz_revisao_id"] = mapping[pick]
        with row1_c3:
            st.session_state["matriz_limit_eq"] = st.number_input(
                "Limite eq.",
                min_value=20,
                max_value=500,
                value=int(st.session_state["matriz_limit_eq"]),
                step=20,
                key="mtz_lim_pick",
            )

        row2_c1, row2_c2, row2_c3 = st.columns([1.05, 1.05, 1.95], vertical_alignment="bottom")
        with row2_c1:
            status_filter = st.selectbox(
                "Status",
                ["Todos", "🔴 Crítico (<50%)", "🟡 Em andamento (50–79%)", "🟢 Avançado (≥80%)", "⬜ Sem dados"],
                index=0,
                key="mtz_status_filter",
            )
        with row2_c2:
            sort_by = st.selectbox(
                "Ordenar",
                ["Nome", "% ↑ (mais atrasados)", "% ↓ (mais avançados)"],
                index=1,
                key="mtz_sort_by",
            )
        with row2_c3:
            actions_left, actions_right = st.columns([0.60, 1.40], gap="medium")
            with actions_left:
                st.session_state["matriz_show_legend"] = st.toggle(
                    "Legenda",
                    value=bool(st.session_state["matriz_show_legend"]),
                    key="mtz_leg",
                )
            with actions_right:
                st.markdown('<div class="mtz-inline-actions">', unsafe_allow_html=True)
                a1, a2, a3 = st.columns([0.90, 1.00, 1.15], gap="small")
                with a1:
                    st.markdown('<div class="mtz-btn-ghost">', unsafe_allow_html=True)
                    clear_dept = st.button(
                        "🧹 Limpar",
                        key="mtz_clear_dept",
                        use_container_width=True,
                        help="Remove o departamento selecionado e mantém os demais filtros.",
                    )
                    st.markdown("</div>", unsafe_allow_html=True)
                with a2:
                    st.markdown('<div class="mtz-btn-neutral">', unsafe_allow_html=True)
                    show_all = st.button(
                        "▦ Ver todos",
                        key="mtz_show_all",
                        use_container_width=True,
                        help="Exibe todos os grupos novamente e limpa a busca atual.",
                    )
                    st.markdown("</div>", unsafe_allow_html=True)
                with a3:
                    st.markdown('<div class="mtz-btn-primary">', unsafe_allow_html=True)
                    reload_data = st.button(
                        "↻ Atualizar",
                        key="mtz_reload",
                        use_container_width=True,
                        help="Recarrega os dados da matriz e atualiza os indicadores.",
                    )
                    st.markdown("</div>", unsafe_allow_html=True)
                st.markdown("</div>", unsafe_allow_html=True)

        if clear_dept:
            st.session_state["matriz_departamento_id"] = None
            st.rerun()
        if show_all:
            st.session_state["matriz_grp_search"] = ""
            st.session_state["matriz_departamento_id"] = None
            st.rerun()
        if reload_data:
            _invalidate_matrix_perf_cache()
            handle_toolbar_reload()
            st.rerun()

        st.markdown("</div></div>", unsafe_allow_html=True)
    return hph, search, status_filter, sort_by


def _render_missing_services_alert(group_ctx):
    missing_svc = {
        sid
        for sid in [str(s.get("id")) for s in group_ctx.all_services if s.get("id")]
        if not any(group_ctx.task_map.get((eid, sid)) for eid in [str(e["id"]) for e in group_ctx.eqs])
    }
    if not missing_svc:
        return
    missing_names = [s.get("nome") or str(s.get("id")) for s in group_ctx.all_services if str(s.get("id")) in missing_svc]
    st.warning(
        f"⚠️ **{len(missing_svc)} serviço(s) sem tarefas geradas** para esta revisão: "
        f"{', '.join(missing_names[:5])}{'...' if len(missing_names) > 5 else ''}. "
        f"Acesse **Admin → Revisões → Sincronizar Matriz** para criar as tarefas faltantes.",
        icon="⚠️",
    )


def _prepare_analytics(group_ctx):
    return _build_cached_analytics(
        eqs_json=json.dumps(group_ctx.eqs, default=str, sort_keys=True),
        setor_to_services_json=json.dumps(group_ctx.setor_to_services, default=str, sort_keys=True),
        task_map_json=_serialize_task_map(group_ctx.task_map),
        atraso_dias_json=json.dumps(group_ctx.group_atraso_dias, default=str, sort_keys=True),
        rev_start_iso=str(group_ctx.group_rev_start),
        pct_geral=float(group_ctx.pct_geral),
        resumo_json=_get_cached_resumo_json(group_ctx),
        semanas_total=int((group_ctx.rev_row or {}).get("semanas_total") or 1),
        now_ref_iso=str(pd.Timestamp(_now_utc()).floor("min")),
    )


def _render_evolucao_tab(group_ctx, base_ctx):
    st.markdown("### Evolução semanal")
    st.caption("Acompanhe o ritmo de conclusão semana a semana versus a meta linear.")
    col_evo1, col_evo2 = st.columns([1, 2])
    with col_evo1:
        rank_mode = st.radio(
            "Escopo:",
            ["Grupo inteiro", "Setor específico"],
            horizontal=False,
            key=f"evo_mode_{group_ctx.revisao_id}_{group_ctx.grupo_id}",
        )
    setor_sel_rank = None
    with col_evo2:
        if rank_mode == "Setor específico":
            setores_rank = sorted(group_ctx.setor_to_services.keys(), key=lambda x: x.lower())
            evo_setor_key = f"evo_setor_val_{group_ctx.grupo_id}"
            evo_default = st.session_state.get(evo_setor_key, setores_rank[0] if setores_rank else None)
            evo_idx = setores_rank.index(evo_default) if evo_default in setores_rank else 0
            setor_sel_rank = st.selectbox(
                "Setor",
                setores_rank,
                index=evo_idx,
                key=f"evo_setor_{group_ctx.revisao_id}_{group_ctx.grupo_id}",
            )
            st.session_state[evo_setor_key] = setor_sel_rank
        else:
            st.caption(
                f"Analisando **{len(group_ctx.eqs)} equipamentos** · **{len(group_ctx.all_services)} serviços** · "
                f"**{len(group_ctx.all_services) * 3 * len(group_ctx.eqs)} etapas** no total"
            )

    chosen = (
        group_ctx.all_services
        if rank_mode == "Grupo inteiro"
        else sorted(group_ctx.setor_to_services.get(setor_sel_rank, []), key=lambda x: (x.get("nome") or "").lower())
    )
    seen = set()
    svc_ids_rank = []
    for s in chosen:
        sid = s.get("id")
        if sid and sid not in seen:
            seen.add(sid)
            svc_ids_rank.append(sid)
    total_cells_rank = int(len(group_ctx.eqs) * max(len(svc_ids_rank), 1) * 3)

    rev_start2 = pd.to_datetime(
        (group_ctx.rev_row or {}).get("data_inicio") or (group_ctx.rev_row or {}).get("created_at"),
        errors="coerce",
        utc=True,
    )
    if pd.isna(rev_start2):
        rev_start2 = pd.Timestamp(_now_utc()).normalize()

    if not group_ctx.tarefas:
        st.info("Sem tarefas para esta revisão/grupo.")
        return

    evo = _build_evo_chart_data(
        tarefas_json=json.dumps(group_ctx.tarefas, default=str),
        svc_ids_rank=tuple(svc_ids_rank),
        total_cells_rank=total_cells_rank,
        semanas_total=int((group_ctx.rev_row or {}).get("semanas_total") or 1),
        rev_start_iso=str(rev_start2),
    )
    if evo is None:
        st.info("Sem tarefas para esta revisão/grupo.")
    elif evo.get("mode") == "no_timestamps":
        st.info("Ainda não há timestamps suficientes para gerar o gráfico.")
    elif evo.get("mode") == "semana_col":
        st.line_chart(pd.DataFrame(evo["cum_vals"]).set_index("Semana"))
    elif evo.get("mode") == "timestamps":
        pc = evo["pc"]
        ps = evo["ps"]
        meta = evo["meta"]
        agg = evo["agg"]
        cum = evo["cum"]
        idx = pc.index

        pct_atual = float(pc["Cumulativo (%)"].iloc[-1]) if not pc.empty else 0
        sem_atual = int(pc.index[-1]) if not pc.empty else 0
        meta_atual = float(meta.iloc[-1]) if len(meta) > 0 else 0
        delta_vs_meta = round(pct_atual - meta_atual, 1)

        mk1, mk2, mk3, mk4 = st.columns(4)
        mk1.metric("Progresso atual", f"{pct_atual:.1f}%")
        mk2.metric(
            "Meta (semana atual)",
            f"{meta_atual:.1f}%",
            delta=f"{delta_vs_meta:+.1f}%",
            delta_color="normal" if delta_vs_meta >= 0 else "inverse",
        )
        mk3.metric("Semanas decorridas", str(sem_atual))
        mk4.metric("Total etapas", f"{int(cum.iloc[-1])}/{total_cells_rank}")
        st.divider()
        _render_altair_evo(pc, ps, meta)
        with st.expander("📋 Tabela detalhada", expanded=False):
            det = pc.join(ps).join(meta).copy()
            det["Concluídos (semana)"] = agg.reindex(idx).fillna(0).astype(int).values
            det["Concluídos (acum.)"] = cum.reindex(idx).ffill().fillna(0).astype(int).values
            st.dataframe(det.reset_index(names="Semana"), use_container_width=True, hide_index=True)
    else:
        st.info("Sem timestamps nem coluna semana disponíveis.")

    st.divider()
    st.markdown("#### Comparativo entre revisões do mesmo grupo")
    st.caption("Compare o progresso desta revisão com revisões anteriores do mesmo grupo.")
    try:
        all_revs = [r for r in base_ctx.revisoes if r.get("id")]
        if len(all_revs) <= 1:
            st.info("Apenas uma revisão encontrada — sem dados para comparar.")
            return
        comp_rows = []
        for rev in all_revs[:6]:
            rid = rev.get("id")
            rtit = rev.get("titulo") or str(rid)[:8]
            try:
                trows = (
                    base_ctx.sb.table("tarefas_servico")
                    .select("etapa_d,etapa_r,etapa_m")
                    .eq("tenant_id", base_ctx.tenant_id)
                    .eq("revisao_id", rid)
                    .in_("equipamento_id", [e["id"] for e in group_ctx.eqs])
                    .execute()
                    .data
                ) or []
                done = sum(int(bool(t.get("etapa_d"))) + int(bool(t.get("etapa_r"))) + int(bool(t.get("etapa_m"))) for t in trows)
                total = max(len(group_ctx.eqs) * len(group_ctx.all_services) * 3, 1)
                comp_rows.append(
                    {
                        "Revisão": rtit,
                        "Status": rev.get("status") or "?",
                        "% Concluído": round((done / total) * 100),
                        "Etapas concluídas": done,
                        "Total esperado": total,
                    }
                )
            except Exception:
                pass
        if comp_rows:
            comp_df = pd.DataFrame(comp_rows)
            comp_df["Atual"] = comp_df.apply(
                lambda r: "◄ atual" if r["Revisão"] in (group_ctx.titulo, group_ctx.revisao_id) else "",
                axis=1,
            )
            st.dataframe(
                comp_df,
                use_container_width=True,
                hide_index=True,
                column_config={"% Concluído": st.column_config.ProgressColumn("% Concluído", min_value=0, max_value=100)},
            )
    except Exception as comp_err:
        st.caption(f"Comparativo não disponível: {comp_err}")


def _render_analytics_tab(group_ctx, analytics_data):
    st.markdown("### Gestão e automação")
    st.caption("Indicadores executivos, riscos do grupo e atalhos operacionais seguros.")
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Progresso geral", f"{analytics_data['progresso_atual_pct']:.0f}%")
    a2.metric(
        "Meta esperada",
        f"{analytics_data['expected_pct_now']:.1f}%",
        delta=f"{analytics_data['delta_vs_expected_now']:+.1f}%",
        delta_color="normal" if analytics_data["delta_vs_expected_now"] >= 0 else "inverse",
    )
    a3.metric("Setores risco alto", sum(1 for item in analytics_data["analytics_sector_intelligence"] if item.get("risk") == "alto"))
    a4.metric("Equip. críticos", analytics_data["critical_eq_count"])

    for insight in analytics_data["automation_insights"][:5]:
        level = str(insight.get("nivel") or "info")
        title = str(insight.get("titulo") or "")
        body = str(insight.get("texto") or "")
        if level == "error":
            st.error(f"**{title}** — {body}")
        elif level == "warning":
            st.warning(f"**{title}** — {body}")
        elif level == "success":
            st.success(f"**{title}** — {body}")
        else:
            st.info(f"**{title}** — {body}")

    b1, b2, b3 = st.columns(3)
    with b1:
        if st.button("Abrir setores críticos", key=f"mtz_auto_open_high_{group_ctx.grupo_id}", use_container_width=True):
            opened = 0
            for item in analytics_data["analytics_priority_sorted"]:
                if item.get("risk") == "alto":
                    _sector_set_open(group_ctx.revisao_id, group_ctx.grupo_id, str(item.get("setor_nome")), True)
                    opened += 1
            if opened:
                st.toast(f"{opened} setor(es) críticos abertos na aba Matriz.")
                st.session_state[f"_mtz_goto_matriz_{group_ctx.grupo_id}"] = True
            else:
                st.toast("Nenhum setor crítico encontrado.")
            st.rerun()
    with b2:
        if st.button("Abrir top 3 prioridades", key=f"mtz_auto_open_top3_{group_ctx.grupo_id}", use_container_width=True):
            opened = 0
            for item in analytics_data["analytics_priority_sorted"][:3]:
                _sector_set_open(group_ctx.revisao_id, group_ctx.grupo_id, str(item.get("setor_nome")), True)
                opened += 1
            if opened:
                st.toast(f"Top {opened} prioridades abertas na aba Matriz.")
                st.session_state[f"_mtz_goto_matriz_{group_ctx.grupo_id}"] = True
            st.rerun()
    with b3:
        if st.button("Fechar setores sob controle", key=f"mtz_auto_close_low_{group_ctx.grupo_id}", use_container_width=True):
            closed = 0
            for item in analytics_data["analytics_sector_intelligence"]:
                if item.get("risk") == "baixo":
                    _sector_set_open(group_ctx.revisao_id, group_ctx.grupo_id, str(item.get("setor_nome")), False)
                    closed += 1
            if closed:
                st.toast(f"{closed} setor(es) sob controle fechados na aba Matriz.")
                st.session_state[f"_mtz_goto_matriz_{group_ctx.grupo_id}"] = True
            st.rerun()

    st.markdown("#### Equipamentos que exigem atenção")
    if group_ctx.resumo_df.empty:
        st.info("Sem dados de equipamentos para análise.")
    else:
        critical_equipment_df = group_ctx.resumo_df.copy()
        critical_equipment_df["Risco"] = critical_equipment_df["%"].apply(
            lambda v: "alto" if int(v) < 50 else ("medio" if int(v) < 80 else "baixo")
        )
        critical_equipment_df = critical_equipment_df.sort_values(by=["%", "Concluidos"], ascending=[True, True]).head(10)[
            ["Equipamento", "%", "Concluidos", "Total", "Risco"]
        ]
        chart_df = critical_equipment_df[["Equipamento", "%", "Risco"]].copy()
        chart_df["cor"] = chart_df["Risco"].map({"alto": "#EF4444", "medio": "#F59E0B", "baixo": "#12B76A"})
        bar = (
            alt.Chart(chart_df)
            .mark_bar(height=18, cornerRadiusEnd=3)
            .encode(
                x=alt.X("%:Q", scale=alt.Scale(domain=[0, 100]), axis=alt.Axis(title="% concluído", grid=True, gridOpacity=0.15)),
                y=alt.Y("Equipamento:N", sort=alt.SortField(field="%", order="ascending"), axis=alt.Axis(title=None, labelLimit=220)),
                color=alt.Color("cor:N", scale=None, legend=None),
                tooltip=[
                    alt.Tooltip("Equipamento:N", title="Equipamento"),
                    alt.Tooltip("%:Q", title="% concluído", format=".0f"),
                    alt.Tooltip("Risco:N", title="Risco"),
                ],
            )
            .properties(height=max(180, len(chart_df) * 32))
            .configure_view(strokeWidth=0)
            .configure_axis(domainOpacity=0.3)
        )
        st.altair_chart(bar, use_container_width=True)
        with st.expander("📋 Ver tabela detalhada", expanded=False):
            st.dataframe(critical_equipment_df, use_container_width=True, hide_index=True)

    st.markdown("#### Lead time médio entre etapas")
    if group_ctx.view_agg.empty:
        st.info("Sem dados de tempo suficientes para calcular lead time.")
        return
    lt_dr = pd.to_numeric(group_ctx.view_agg.get("D→R (h)"), errors="coerce")
    lt_rm = pd.to_numeric(group_ctx.view_agg.get("R→M (h)"), errors="coerce")
    lt_dm = pd.to_numeric(group_ctx.view_agg.get("D→M (h)"), errors="coerce")
    l1, l2, l3 = st.columns(3)
    l1.metric("Mediana D→R", _fmt_duration_from_hours(lt_dr.dropna().median() if lt_dr is not None and not lt_dr.dropna().empty else None))
    l2.metric("Mediana R→M", _fmt_duration_from_hours(lt_rm.dropna().median() if lt_rm is not None and not lt_rm.dropna().empty else None))
    l3.metric("Mediana D→M", _fmt_duration_from_hours(lt_dm.dropna().median() if lt_dm is not None and not lt_dm.dropna().empty else None))


def _resolve_selected_group(base_ctx, search: str, status_filter: str, sort_by: str):
    if render_selection_screen(
        tenant_id=base_ctx.tenant_id,
        revisao_id=st.session_state.get("matriz_revisao_id"),
        grupos=base_ctx.grupos,
        search=search,
        status_filter=status_filter,
        sort_by=sort_by,
        data_version=st.session_state.get("data_version", "0"),
    ):
        return None

    return _get_cached_group_context(base_ctx)


def _render_group_overview(base_ctx, group_ctx, header_placeholder) -> dict:
    if st.session_state.get("matriz_show_legend"):
        st.markdown("**Legenda:** pendente · em andamento · concluido · travado · nao aplica")

    _render_missing_services_alert(group_ctx)

    render_group_header(
        placeholder=header_placeholder,
        grupo_nome=group_ctx.grupo_nome,
        titulo=group_ctx.titulo,
        eqs=group_ctx.eqs,
        pct_geral=group_ctx.pct_geral,
        eq100_g=group_ctx.eq100_g,
        setor_rows=group_ctx.setor_rows,
        revisao_id=group_ctx.revisao_id,
        grupo_id=group_ctx.grupo_id,
    )

    return _prepare_analytics(group_ctx)


def _get_sections(can_edit: bool) -> list[str]:
    sections = ["📊 Resumo", "⚙️ Matriz", "📈 Evolução", "🧠 Analytics", "⏱️ Tempos"]
    if can_edit:
        sections.append("✏️ Editar célula")
    sections.append("⬇️ Exportar")
    return sections


def _section_state_key(group_ctx) -> str:
    return f"mtz_active_section_{group_ctx.revisao_id}_{group_ctx.grupo_id}"


def _open_matrix_section_if_needed(group_ctx) -> None:
    if st.session_state.pop(f"_mtz_goto_matriz_{group_ctx.grupo_id}", False):
        st.session_state[_section_state_key(group_ctx)] = "⚙️ Matriz"


def _sync_pdf_group_signature(base_ctx, group_ctx) -> None:
    early_signature = (str(base_ctx.tenant_id), str(group_ctx.grupo_id), str(group_ctx.revisao_id))
    if st.session_state.get("_mtz_pdf_grupo_sig") != early_signature:
        st.session_state.pop("mtz_pdf_export_bytes", None)
        st.session_state.pop("mtz_pdf_export_signature", None)
        st.session_state["_mtz_pdf_grupo_sig"] = early_signature


def _render_resumo_section(group_ctx) -> None:
    render_summary_tab(resumo_df=group_ctx.resumo_df)


def _render_matriz_section(base_ctx, group_ctx) -> None:
    from .matrix_tab import render_matrix_tab

    render_matrix_tab(
        sb=base_ctx.sb,
        revisao_id=group_ctx.revisao_id,
        grupo_id=group_ctx.grupo_id,
        group_atraso_dias=group_ctx.group_atraso_dias,
        semanas_disp=group_ctx.semanas_disp,
        semana_sugerida=group_ctx.semana_sugerida,
        group_rev_start=group_ctx.group_rev_start,
        setor_to_services=group_ctx.setor_to_services,
        tarefas=group_ctx.tarefas,
        eqs=group_ctx.eqs,
        task_map=group_ctx.task_map,
        eq_label_short=group_ctx.eq_label_short,
    )


def _render_tempos_section(base_ctx, group_ctx) -> None:
    from .tempos_tab import render_tempos_tab

    render_tempos_tab(
        sb=base_ctx.sb,
        tenant_id=base_ctx.tenant_id,
        revisao_id=group_ctx.revisao_id,
        eq_ids=group_ctx.eq_ids,
        tarefas=group_ctx.tarefas,
        svc_ids_rank=[],
        svc_ids_all=group_ctx.svc_ids_all,
        all_services=group_ctx.all_services,
        eq_label_short=group_ctx.eq_label_short,
        eq_label=group_ctx.eq_label,
    )


def _render_editor_section(base_ctx, group_ctx) -> None:
    from .editor_tab import render_bulk_editor, render_editor_tab

    edit_mode = st.radio(
        "Modo de edição",
        ["✏️ Célula individual", "⚡ Lote por serviço"],
        horizontal=True,
        key="mat_edit_mode",
    )
    st.divider()
    if edit_mode == "✏️ Célula individual":
        render_editor_tab(
            sb=base_ctx.sb,
            tenant_id=base_ctx.tenant_id,
            revisao_id=group_ctx.revisao_id,
            grupo_id=group_ctx.grupo_id,
            setor_to_services=group_ctx.setor_to_services,
            eq_label_short=group_ctx.eq_label_short,
            task_map=group_ctx.task_map,
            semana_sugerida=group_ctx.semana_sugerida,
            eq_ocultos_set=group_ctx.eq_ocultos_set,
        )
    else:
        render_bulk_editor(
            sb=base_ctx.sb,
            tenant_id=base_ctx.tenant_id,
            revisao_id=group_ctx.revisao_id,
            setor_to_services=group_ctx.setor_to_services,
            task_map=group_ctx.task_map,
            eqs=group_ctx.eqs,
            eq_label_short=group_ctx.eq_label_short,
            semana_sugerida=group_ctx.semana_sugerida,
        )


def _render_export_section(base_ctx, group_ctx) -> None:
    from .export_tab import render_export_tab

    render_export_tab(
        tenant_id=base_ctx.tenant_id,
        grupo_id=group_ctx.grupo_id,
        revisao_id=group_ctx.revisao_id,
        titulo=group_ctx.titulo,
        grupo_nome=group_ctx.grupo_nome,
        resumo_df=group_ctx.resumo_df,
        view_agg=group_ctx.view_agg,
        sector_tables_for_export=group_ctx.sector_tables_for_export,
        data_version=st.session_state.get("data_version", "0"),
    )


def _render_section_switcher(group_ctx, can_edit: bool) -> str:
    options = _get_sections(can_edit)
    state_key = _section_state_key(group_ctx)
    st.session_state.setdefault(state_key, "⚙️ Matriz")

    current_value = st.session_state.get(state_key, "⚙️ Matriz")
    if current_value not in options:
        current_value = options[0]
        st.session_state[state_key] = current_value

    st.markdown("### Navegação rápida")
    try:
        picked = st.segmented_control(
            "Seção",
            options=options,
            selection_mode="single",
            default=current_value,
            key=f"{state_key}_segmented",
            label_visibility="collapsed",
        )
        if picked:
            st.session_state[state_key] = picked
    except Exception:
        picked = st.radio(
            "Seção",
            options=options,
            index=options.index(current_value),
            horizontal=True,
            key=f"{state_key}_radio",
            label_visibility="collapsed",
        )
        st.session_state[state_key] = picked

    return st.session_state[state_key]


def _render_active_section(base_ctx, group_ctx, analytics_data, active_section: str) -> None:
    if active_section == "📊 Resumo":
        _render_resumo_section(group_ctx)
    elif active_section == "⚙️ Matriz":
        _render_matriz_section(base_ctx, group_ctx)
    elif active_section == "📈 Evolução":
        _render_evolucao_tab(group_ctx, base_ctx)
    elif active_section == "🧠 Analytics":
        _render_analytics_tab(group_ctx, analytics_data)
    elif active_section == "⏱️ Tempos":
        _render_tempos_section(base_ctx, group_ctx)
    elif active_section == "✏️ Editar célula":
        _render_editor_section(base_ctx, group_ctx)
    elif active_section == "⬇️ Exportar":
        _render_export_section(base_ctx, group_ctx)


def _render_sections(base_ctx, group_ctx, analytics_data) -> None:
    _open_matrix_section_if_needed(group_ctx)
    _sync_pdf_group_signature(base_ctx, group_ctx)
    active_section = _render_section_switcher(group_ctx, base_ctx.can_edit)
    st.divider()
    _render_active_section(base_ctx, group_ctx, analytics_data, active_section)


def render_matriz() -> None:
    try:
        _inject_css()
        _ph("⊞", "Matriz de Atividades", "Visao por Grupo com drill-down por Setor. Etapas D/R/M, tempos e exportacoes.")

        base_ctx = load_matrix_base_context()
        if not base_ctx:
            return

        header_placeholder, search, status_filter, sort_by = _render_toolbar(base_ctx)
        group_ctx = _resolve_selected_group(base_ctx, search, status_filter, sort_by)
        if not group_ctx:
            return

        analytics_data = _render_group_overview(base_ctx, group_ctx, header_placeholder)
        _render_sections(base_ctx, group_ctx, analytics_data)
    except Exception as e:
        st.error("Erro ao renderizar a Matriz.")
        st.exception(e)
