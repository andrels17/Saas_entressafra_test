"""Renderização principal da Matriz Operacional (módulo modularizado)."""
from __future__ import annotations

import time
from datetime import date, datetime, timezone

import altair as alt
import pandas as pd
import streamlit as st

from src.auth.roles import Role
from src.auth.scope import get_my_scope
from src.auth.permissions import can_edit_matriz, can_view_all_data
from src.ui.core.styles import page_header as _ph
from src.ui.core.cache import bump_data_version, clear_cached_functions
from src.ui.components.forms import form_submit_button, validation_summary
from src.ui.components.confirmations import confirmation_panel
from src.utils import nav
from src.utils.supabase_helpers import (
    normalize_id,
    current_role,
    current_tenant_id,
    current_user_id,
    sb_for_user,
)
from src.utils.timezone import now_utc as _now_utc, now_brt as _now_brt
from src.utils.weeks import week_from_revisao as _week_from_revisao
from src.ui.pages.matriz_sector import (
    build_change_preview_lines,
    build_sector_frame,
    sector_progress_label,
    sector_summary_metrics,
    summarize_sector_intelligence,
)
from src.ui.pages.matriz_runtime import (
    build_task_maps as _build_task_maps,
    bulk_update_tasks as _bulk_update_tasks,
    eq_label_map as _eq_label_map,
    filter_obs_map_for_sector as _filter_obs_map_for_sector,
    normalize_service_ids as _normalize_service_ids,
    risk_color as _risk_color,
    risk_score as _risk_score,
    sector_is_open as _sector_is_open,
    sector_set_open as _sector_set_open,
    svc_name_map as _svc_name_map,
    task_key as _task_key,
)

from .data import _all_dept_names, _dept_name, _fetch_template, _group_kpis, _load_payload
from .insights import _build_automation_insights, _build_group_sector_intelligence, _fmt_duration_from_hours, _sector_priority_sort_key
from .pdf_export import _build_pdf_tables, _compute_setor_ok_counts, _df_to_csv_bytes, _reportlab_available, _style_heatmap
from .styles import (
    _build_group_card_html,
    _build_group_card_label,
    _card_status_meta,
    _compact_card_summary,
    _inject_css,
)
from .selection import render_selection_screen
from .header import render_group_header
from .summary_tab import render_summary_tab

@st.cache_data(ttl=60, show_spinner=False)
def _build_evo_chart_data(
    tarefas_json: str,
    svc_ids_rank: tuple[str, ...],
    total_cells_rank: int,
    semanas_total: int,
    rev_start_iso: str,
) -> dict | None:
    """Transforma tarefas em séries para o gráfico de evolução — cacheado por 60s.

    Recebe tarefas como JSON string para ser hashável pelo cache do Streamlit.
    Retorna dict com pc, ps, meta, agg, cum — ou None se não houver dados.
    """
    import json

    tarefas = json.loads(tarefas_json)
    df = pd.DataFrame(tarefas)
    if df.empty:
        return None

    rev_start = pd.to_datetime(rev_start_iso, utc=True)
    svc_set = set(svc_ids_rank)

    def _wk(s):
        dt = pd.to_datetime(s, errors="coerce", utc=True)
        return ((dt - rev_start).dt.days.clip(lower=0) // 7 + 1).astype("Int64")

    has_dt = any(
        c in df.columns and df[c].notna().any()
        for c in ["dt_etapa_d", "dt_etapa_r", "dt_etapa_m"]
    )

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
        meta = pd.Series(
            [min(100.0, (w / wt) * 100) for w in idx],
            index=idx, name="Meta (%)",
        )
        return {"mode": "timestamps", "pc": pc, "ps": ps, "meta": meta,
                "agg": agg, "cum": cum}

    if "semana" in df.columns:
        df_done = df[df["servico_id"].isin(svc_set) & df["semana"].notna()].copy()
        if df_done.empty:
            return None
        df_done["semana"] = pd.to_numeric(df_done["semana"], errors="coerce").astype("Int64")
        df_done = df_done.dropna(subset=["semana"])
        cum_vals = []
        for w in sorted(df_done["semana"].unique()):
            w_df = df_done[df_done["semana"] <= w]
            ok_w = int(
                w_df[["etapa_d", "etapa_r", "etapa_m"]]
                .fillna(False).astype(bool).astype(int).sum().sum()
            )
            cum_vals.append({"Semana": int(w), "% Concluído": round(
                (ok_w / max(total_cells_rank, 1)) * 100, 1)})
        return {"mode": "semana_col", "cum_vals": cum_vals}

    return None


def _render_altair_evo(pc: pd.DataFrame, ps: pd.DataFrame,
                       meta: pd.Series) -> None:
    """Renderiza o gráfico de evolução com Altair (hover rico + crosshair)."""
    df_chart = pc.join(ps).join(meta).reset_index(names="Semana")
    df_melt = df_chart.melt("Semana", var_name="série", value_name="valor")

    # Delta vs meta por semana (calculado no Vega-Lite via transform)
    df_meta = df_chart[["Semana", "Meta (%)"]].rename(columns={"Meta (%)": "meta_val"})
    df_melt = df_melt.merge(df_meta, on="Semana", how="left")
    df_melt["delta"] = (df_melt["valor"] - df_melt["meta_val"]).round(1)

    nearest = alt.selection_point(
        nearest=True, on="mouseover", fields=["Semana"], empty=False
    )

    color_scale = alt.Scale(
        domain=["Cumulativo (%)", "Meta (%)", "Na semana (%)"],
        range=["#7F77DD", "#378ADD", "#D85A30"],
    )

    base = alt.Chart(df_melt).encode(
        x=alt.X("Semana:Q", axis=alt.Axis(tickMinStep=1, title="Semana")),
        y=alt.Y(
            "valor:Q",
            axis=alt.Axis(title="%", format=".1f"),
            scale=alt.Scale(domain=[0, 100]),
        ),
        color=alt.Color(
            "série:N",
            scale=color_scale,
            legend=alt.Legend(orient="bottom", title=None),
        ),
    )

    lines = base.mark_line(
        strokeWidth=2,
        point=alt.OverlayMarkDef(size=40, filled=True),
    )

    points_highlight = base.mark_point(
        size=80, filled=True, opacity=0,
    ).encode(
        opacity=alt.condition(nearest, alt.value(1), alt.value(0)),
        tooltip=[
            alt.Tooltip("Semana:Q", title="Semana"),
            alt.Tooltip("série:N", title="Série"),
            alt.Tooltip("valor:Q", title="%", format=".1f"),
            alt.Tooltip("delta:Q", title="vs meta", format="+.1f"),
        ],
    ).add_params(nearest)

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


def _pct_bar_html(pct, height=4):
    """Barra de progresso HTML inline — não depende do módulo legado."""
    try:
        pct = max(0.0, min(100.0, float(pct or 0)))
    except Exception:
        pct = 0.0
    return (
        f'<div style="width:100%;height:{int(height)}px;background:rgba(255,255,255,.08);'
        f'border-radius:999px;overflow:hidden;">'
        f'<div style="width:{pct:.1f}%;height:100%;'
        f'background:linear-gradient(90deg,#22c55e,#eab308,#ef4444);"></div></div>'
    )



def _collect_matrix_changes(df_display, edited, svc_bool, col_meta):
    """Coleta mudanças do data editor de forma compatível com índices ocultos/RangeIndex."""
    if edited is None:
        return []

    changes = []
    source_ids = list(df_display.index)

    def _row_value(row, col):
        try:
            return bool(row[col])
        except Exception:
            try:
                return bool(getattr(row, col))
            except Exception:
                return False

    try:
        edited_rows = list(edited.iterrows())
    except Exception:
        edited_rows = []

    if not edited_rows:
        return []

    for pos, (row_idx, row) in enumerate(edited_rows):
        equip_id = row_idx if row_idx in df_display.index else None
        if equip_id is None and pos < len(source_ids):
            equip_id = source_ids[pos]
        if equip_id is None or equip_id not in df_display.index:
            continue

        for col in svc_bool:
            if col not in col_meta:
                continue
            ov = bool(df_display.loc[equip_id, col])
            nv = _row_value(row, col)
            if ov != nv:
                sid, field = col_meta[col]
                changes.append((str(equip_id), str(sid), field, nv))

    return changes

def render_matriz() -> None:
    try:
        _inject_css()
        _ph("\u229e", "Matriz de Atividades",
            "Visao por Grupo com drill-down por Setor. Etapas D/R/M, tempos e exportacoes.")

        tenant_id = current_tenant_id()
        sb = sb_for_user()
        role = current_role()

        # Melhoria 1: scope
        dep_scope_ids, grp_scope_ids = get_my_scope(tenant_id)
        can_view_all = can_view_all_data(role)
        can_edit = can_edit_matriz(str(role).strip().lower())
        if not can_view_all and dep_scope_ids == [] and grp_scope_ids == []:
            st.warning("Você não possui departamentos ou grupos vinculados para visualizar a matriz.")
            return

        st.session_state.setdefault("data_version", "0")
        st.session_state.setdefault("matriz_view", "select")
        st.session_state.setdefault("matriz_limit_eq", 120)
        st.session_state.setdefault("matriz_show_legend", False)
        st.session_state.setdefault("matriz_departamento_id", None)
        st.session_state.setdefault("matriz_atraso_dias", 7)

        revisoes = (
            sb.table("revisoes").select("id,titulo,status,created_at,data_inicio,semanas_total") .eq(
                "tenant_id",
                tenant_id).order(
                "created_at",
                desc=True).execute().data) or []

        gq = sb.table("equip_grupos").select("id,nome,departamento_id").eq(
            "tenant_id", tenant_id).eq("ativo", True).order("nome")
        if not can_view_all and dep_scope_ids is not None:
            gq = (
                gq.eq(
                    "departamento_id",
                    dep_scope_ids[0]) if len(dep_scope_ids) == 1 else gq.in_(
                    "departamento_id",
                    dep_scope_ids))
        grupos = gq.execute().data or []
        if not can_view_all and grp_scope_ids is not None:
            grupos = [g for g in grupos if g["id"] in grp_scope_ids]
        if not grupos:
            st.info("Nenhum grupo disponivel para o seu escopo.")
            return

        if "matriz_revisao_id" not in st.session_state:
            ativa = next(
                (r for r in revisoes if r.get("status") == "ativa"), None)
            st.session_state["matriz_revisao_id"] = (
                ativa["id"] if ativa else (
                    revisoes[0]["id"] if revisoes else None))
        if "matriz_grupo_id" not in st.session_state:
            st.session_state["matriz_grupo_id"] = grupos[0]["id"]

        hph = st.empty()

        # Toolbar compacta inicial (tela de seleção)
        with hph.container():
            st.markdown('<div class="enterprise-sticky">', unsafe_allow_html=True)
            st.markdown('<div class="enterprise-title">Matriz Operacional</div>', unsafe_allow_html=True)
            st.markdown('<div class="enterprise-sub">Filtros, revisão e acesso rápido aos grupos</div>', unsafe_allow_html=True)

            _clear_dept = False
            _show_all = False
            _reload = False

            row1_c1, row1_c2, row1_c3 = st.columns([1.7, 1.1, 0.7], vertical_alignment="bottom")
            with row1_c1:
                st.session_state.setdefault("matriz_grp_search", "")
                search = st.text_input(
                    "Buscar",
                    value=st.session_state["matriz_grp_search"],
                    placeholder="Grupo ou departamento…",
                    key="mtz_search_in")
                st.session_state["matriz_grp_search"] = search
            with row1_c2:
                rev_opts = [
                    (r.get("titulo") or f"Revisao {r['id']}", r["id"])
                    for r in revisoes if r.get("id")]
                if not rev_opts:
                    st.selectbox("Revisao", ["Nenhuma revisao"], disabled=True, key="rev_pick_dis")
                else:
                    rlbls = [lbl for lbl, _ in rev_opts]
                    rmap = {lbl: rid for lbl, rid in rev_opts}
                    cur = next((lbl for lbl, rid in rev_opts if rid == st.session_state["matriz_revisao_id"]), rlbls[0])
                    pick = st.selectbox("Revisao", rlbls, index=rlbls.index(cur), key="mtz_rev_pick")
                    st.session_state["matriz_revisao_id"] = rmap[pick]
            with row1_c3:
                st.session_state["matriz_limit_eq"] = st.number_input(
                    "Limite eq.", min_value=20, max_value=500, value=int(
                        st.session_state["matriz_limit_eq"]), step=20, key="mtz_lim_pick")

            row2_c1, row2_c2, row2_c3 = st.columns([1.05, 1.05, 1.95], vertical_alignment="bottom")
            with row2_c1:
                _status_filter = st.selectbox(
                    "Status",
                    ["Todos", "🔴 Crítico (<50%)", "🟡 Em andamento (50–79%)", "🟢 Avançado (≥80%)", "⬜ Sem dados"],
                    index=0, key="mtz_status_filter")
            with row2_c2:
                _sort_by = st.selectbox(
                    "Ordenar",
                    ["Nome", "% ↑ (mais atrasados)", "% ↓ (mais avançados)"],
                    index=1, key="mtz_sort_by")
            with row2_c3:
                actions_left, actions_right = st.columns([0.60, 1.40], gap="medium")
                with actions_left:
                    st.session_state["matriz_show_legend"] = st.toggle(
                        "Legenda", value=bool(st.session_state["matriz_show_legend"]), key="mtz_leg")
                with actions_right:
                    st.markdown('<div class="mtz-inline-actions">', unsafe_allow_html=True)
                    a1, a2, a3 = st.columns([0.90, 1.00, 1.15], gap="small")
                    with a1:
                        st.markdown('<div class="mtz-btn-ghost">', unsafe_allow_html=True)
                        _clear_dept = st.button(
                            "🧹 Limpar",
                            key="mtz_clear_dept",
                            use_container_width=True,
                            help="Remove o departamento selecionado e mantém os demais filtros.",
                        )
                        st.markdown('</div>', unsafe_allow_html=True)
                    with a2:
                        st.markdown('<div class="mtz-btn-neutral">', unsafe_allow_html=True)
                        _show_all = st.button(
                            "▦ Ver todos",
                            key="mtz_show_all",
                            use_container_width=True,
                            help="Exibe todos os grupos novamente e limpa a busca atual.",
                        )
                        st.markdown('</div>', unsafe_allow_html=True)
                    with a3:
                        st.markdown('<div class="mtz-btn-primary">', unsafe_allow_html=True)
                        _reload = st.button(
                            "↻ Atualizar",
                            key="mtz_reload",
                            use_container_width=True,
                            help="Recarrega os dados da matriz e atualiza os indicadores.",
                        )
                        st.markdown('</div>', unsafe_allow_html=True)
                    st.markdown('</div>', unsafe_allow_html=True)

            if _clear_dept:
                st.session_state["matriz_departamento_id"] = None
                st.rerun()
            if _show_all:
                st.session_state["matriz_grp_search"] = ""
                st.session_state["matriz_departamento_id"] = None
                st.rerun()
            if _reload:
                bump_data_version()
                clear_cached_functions(
                    _load_payload,
                    _group_kpis,
                    _all_dept_names,
                    _build_task_maps,
                    _filter_obs_map_for_sector,
                    _normalize_service_ids,
                )
                st.rerun()

            st.markdown('</div></div>', unsafe_allow_html=True)

        # Tela de selecao — cards com barra de progresso (Melhoria 3)
        if render_selection_screen(
            tenant_id=tenant_id,
            revisao_id=st.session_state.get("matriz_revisao_id"),
            grupos=grupos,
            search=search,
            status_filter=_status_filter,
            sort_by=_sort_by,
            data_version=st.session_state.get("data_version", "0"),
        ):
            return

        # ── Visao do grupo ──
        grupo_id = st.session_state["matriz_grupo_id"]
        revisao_id = st.session_state["matriz_revisao_id"]
        limit_eq = int(st.session_state["matriz_limit_eq"])
        if not revisao_id:
            st.warning("Nenhuma revisao selecionada.")
            return

        # FIX TROCA DE GRUPO: se o grupo mudou desde o último render,
        # limpa o cache de dados para garantir que _load_payload busque do
        # banco.
        _last_rendered_grupo = st.session_state.get(
            "_mtz_last_rendered_grupo_id")
        if _last_rendered_grupo != grupo_id:
            try:
                _load_payload.clear()
            except Exception:
                clear_cached_functions(_load_payload)
            st.session_state["_mtz_last_rendered_grupo_id"] = grupo_id
            # Limpa payload cacheado manualmente no session_state
            st.session_state.pop("_mtz_payload_cache", None)

        if not can_view_all and grp_scope_ids is not None and grupo_id not in grp_scope_ids:
            st.warning("Voce nao tem acesso a este grupo.")
            if st.button("Voltar", key="mtz_back_noaccess"):
                st.session_state["matriz_view"] = "select"
                st.rerun()
            return

        rev_row = next(
            (r for r in revisoes if r.get("id") == revisao_id), None)
        titulo = (rev_row.get("titulo") if rev_row else None) or "Revisao"
        grupo_nome = next(
            (g.get("nome") for g in grupos if g.get("id") == grupo_id),
            str(grupo_id))

        if st.session_state.get("matriz_show_legend"):
            st.markdown(
                "**Legenda:** pendente · em andamento · concluido · travado · nao aplica")

        # Carrega payload — usa cache manual no session_state keyed por grupo_id
        # para garantir que troca de grupo sempre busca dados corretos do
        # banco.
        _payload_cache = st.session_state.get("_mtz_payload_cache") or {}
        _payload_key = (str(tenant_id), str(grupo_id), str(revisao_id), str(
            limit_eq), str(st.session_state.get("data_version", "0")))
        if _payload_cache.get("key") != str(_payload_key):
            _payload_cache = {
                "key": str(_payload_key),
                "data": _load_payload(
                    tenant_id,
                    grupo_id,
                    revisao_id,
                    limit_eq,
                    st.session_state.get("data_version", "0"),
                    st.session_state.get("sb_access_token", ""),
                ),
            }
            st.session_state["_mtz_payload_cache"] = _payload_cache
        payload = _payload_cache["data"]
        eqs = payload.get("eqs") or []
        if not eqs:
            st.info("Nenhum equipamento no grupo.")
            if st.button("Voltar", key="mtz_back_noeq"):
                st.session_state["matriz_view"] = "select"

                st.rerun()
            return

        # Carrega equipamentos ocultos desta revisão (para marcar visualmente)
        try:
            from src.utils.eq_oculto import get_ocultos as _get_ocultos
            _eq_ocultos_set = _get_ocultos(sb, tenant_id, revisao_id)
        except Exception:
            _eq_ocultos_set = set()

        eq_ids = [e["id"] for e in eqs]
        # eq_label: descricao completa — Resumo e PDF
        eq_label = {
            e["id"]: (
                f"⊘ {e.get('frota', '')} — {e.get('modelo') or ''}".strip(" —")
                if e["id"] in _eq_ocultos_set
                else f"{e.get('frota', '')} — {e.get('modelo') or ''}".strip(" —")
            ) for e in eqs}
        # eq_label_short: apenas o numero/frota — Matriz, Tempos, Editor
        eq_label_short = {
            e["id"]: (
                f"⊘ {(str(e.get('frota') or '')).strip() or str(e.get('id', ''))}"
                if e["id"] in _eq_ocultos_set
                else (str(e.get("frota") or "")).strip() or str(e.get("id", ""))
            ) for e in eqs}
        setor_to_services = payload.get("s2s") or {}
        all_services = payload.get("all_s") or []

        if not all_services:
            try:
                s2s2, all2 = _fetch_template(sb, tenant_id, grupo_id)
                if all2:
                    setor_to_services, all_services = s2s2, all2
                    bump_data_version()
                    clear_cached_functions(_load_payload, _group_kpis, _all_dept_names)
                else:
                    st.warning(
                        "Grupo sem Template configurado (Admin > Templates).")
                    if st.button("Voltar", key="mtz_back_notpl"):
                        st.session_state["matriz_view"] = "select"
                        st.rerun()
                    return
            except Exception:
                st.warning(
                    "Grupo sem Template configurado (Admin > Templates).")
                if st.button("Voltar", key="mtz_back_notpl2"):
                    st.session_state["matriz_view"] = "select"
                    st.rerun()
                return

        tarefas = payload.get("tarefas") or []
        task_map = {(str(t["equipamento_id"]), str(t["servico_id"])): t for t in tarefas}

        # ── Aviso de serviços sem tarefa (Item 6) ──
        _svc_ids_all_check = [str(s.get("id")) for s in all_services if s.get("id")]
        _eq_ids_str = [str(e["id"]) for e in eqs]
        _missing_svc = {
            sid for sid in _svc_ids_all_check
            if not any(task_map.get((eid, sid)) for eid in _eq_ids_str)
        }
        if _missing_svc:
            _missing_names = [
                s.get("nome") or str(s.get("id"))
                for s in all_services
                if str(s.get("id")) in _missing_svc
            ]
            st.warning(
                f"⚠️ **{len(_missing_svc)} serviço(s) sem tarefas geradas** para esta revisão: "
                f"{', '.join(_missing_names[:5])}{'...' if len(_missing_names) > 5 else ''}. "
                f"Acesse **Admin → Revisões → Sincronizar Matriz** para criar as tarefas faltantes.",
                icon="⚠️",
            )

        # Melhoria 7: svc_ids_all antes das tabs
        svc_ids_all = [s.get("id") for s in all_services if s.get("id")]
        semanas_disp = sorted({int(t.get("semana") or 0)
                              for t in tarefas if t.get("semana")})

        # Semana sugerida: calculada a partir da data_inicio da revisão (BRT)
        _rev_data_inicio = None
        _rev_semanas_total = None
        try:
            if rev_row and rev_row.get("data_inicio"):
                _rev_data_inicio = date.fromisoformat(
                    str(rev_row["data_inicio"])[:10])
            _rev_semanas_total = int(
                rev_row.get("semanas_total") or 0) or None if rev_row else None
        except Exception:
            pass  # ignorado — operação opcional
        _semana_sugerida = _week_from_revisao(
            _now_brt().date(), _rev_data_inicio, _rev_semanas_total)

        total_per_eq = max(len(all_services), 1) * 3
        resumo_rows = []
        tok_g = 0
        eq100_g = 0
        for e in eqs:
            done = sum(int(bool((task_map.get((e["id"], s.get("id"))) or {}).get(
                f))) for s in all_services if s.get("id") for f in ("etapa_d", "etapa_r", "etapa_m"))
            pct = round((done / max(total_per_eq, 1)) * 100)
            resumo_rows.append({"Score": _risk_score(pct), "%": pct, "Equipamento": eq_label.get(
                e["id"], str(e.get("id"))), "Concluidos": int(done), "Total": int(total_per_eq)})
            tok_g += done
            if done >= (len(all_services) * 3):
                eq100_g += 1
        resumo_df = pd.DataFrame(resumo_rows)
        if not resumo_df.empty:
            resumo_df = resumo_df.sort_values(["Score", "%", "Equipamento"], ascending=[
                                              False, True, True]).reset_index(drop=True)

        pct_geral = round(
            (tok_g / max(len(eqs) * len(all_services) * 3, 1)) * 100)
        setor_rows = _compute_setor_ok_counts(eqs, setor_to_services, task_map)
        # Header com barra de progresso
        render_group_header(
            placeholder=hph,
            grupo_nome=grupo_nome,
            titulo=titulo,
            eqs=eqs,
            pct_geral=pct_geral,
            eq100_g=eq100_g,
            setor_rows=setor_rows,
            revisao_id=revisao_id,
            grupo_id=grupo_id,
        )


        group_atraso_dias = int(st.session_state.get("matriz_atraso_dias", 7) or 7)
        group_rev_start = pd.to_datetime(
            (rev_row or {}).get("data_inicio") or (rev_row or {}).get("created_at"),
            errors="coerce",
            utc=True,
        )
        if pd.isna(group_rev_start):
            group_rev_start = pd.Timestamp(_now_utc()).normalize()

        analytics_sector_intelligence = _build_group_sector_intelligence(
            equipamentos=eqs,
            setor_to_services=setor_to_services,
            task_map=task_map,
            atraso_dias=group_atraso_dias,
            rev_start=group_rev_start,
        )
        analytics_priority_sorted = sorted(
            analytics_sector_intelligence,
            key=_sector_priority_sort_key,
        )

        elapsed_days = 0
        try:
            elapsed_days = max(
                0,
                int((pd.Timestamp(_now_utc()).tz_convert("UTC") - group_rev_start).days),
            )
        except Exception:
            elapsed_days = 0
        current_week_no = int(elapsed_days // 7 + 1)
        total_weeks_plan = int((rev_row or {}).get("semanas_total") or current_week_no or 1)
        expected_pct_now = round(min(100.0, (current_week_no / max(total_weeks_plan, 1)) * 100), 1)
        progresso_atual_pct = float(pct_geral)
        delta_vs_expected_now = round(progresso_atual_pct - expected_pct_now, 1)

        critical_eq_count = int((resumo_df["%"] < 50).sum()) if not resumo_df.empty else 0
        no_start_eq_count = int((resumo_df["%"] == 0).sum()) if not resumo_df.empty else 0

        automation_insights = _build_automation_insights(
            sector_intelligence=analytics_sector_intelligence,
            progresso_atual=progresso_atual_pct,
            meta_atual=expected_pct_now,
            critical_eq_count=critical_eq_count,
            no_start_eq_count=no_start_eq_count,
        )

        tab_labels = ["📊 Resumo", "⚙️ Matriz", "📈 Evolução", "🧠 Analytics", "⏱️ Tempos"]
        if can_edit:
            tab_labels.append("✏️ Editar célula")
        tab_labels.append("⬇️ Exportar")
        _tabs = st.tabs(tab_labels)
        if can_edit:
            tab_resumo, tab_matriz, tab_evolucao, tab_analytics, tab_tempos, tab_editor, tab_exportar = _tabs
        else:
            tab_resumo, tab_matriz, tab_evolucao, tab_analytics, tab_tempos, tab_exportar = _tabs
            tab_editor = None

        # FIX #3 e #8: pré-computar dados de export ANTES das tabs
        # Assim Exportar funciona mesmo sem o usuário ter visitado Matriz ou
        # Tempos

        # FIX GRUPO: invalidar bytes de PDF cacheados ANTES das tabs,
        # para garantir que trocar de grupo sempre gera um novo PDF.
        _early_signature = (str(tenant_id), str(grupo_id), str(revisao_id))
        if st.session_state.get("_mtz_pdf_grupo_sig") != _early_signature:
            st.session_state.pop("mtz_pdf_export_bytes", None)
            st.session_state.pop("mtz_pdf_export_signature", None)
            st.session_state["_mtz_pdf_grupo_sig"] = _early_signature

        sector_tables_for_export = []
        for _sn in sorted(setor_to_services.keys()):
            _svs = sorted(
                setor_to_services[_sn],
                key=lambda x: (
                    x.get("nome") or "").lower())
            _sids = [s["id"] for s in _svs if s.get("id")]
            _snames = [s.get("nome") or str(s.get("id"))
                       for s in _svs if s.get("id")]
            if not _sids:
                continue
            _rows = []
            for e in eqs:
                _row = {"Equipamento": eq_label_short[e["id"]]}
                for sid, sname in zip(_sids, _snames):
                    t = task_map.get((e["id"], sid)) or {}
                    _row[f"{sname} D"] = "OK" if t.get("etapa_d") else ""
                    _row[f"{sname} R"] = "OK" if t.get("etapa_r") else ""
                    _row[f"{sname} M"] = "OK" if t.get("etapa_m") else ""
                _rows.append(_row)
            if _rows:
                sector_tables_for_export.append((_sn, pd.DataFrame(_rows)))

        # FIX #8: pré-computar view_agg para CSV de tempos (independente de
        # visitar a aba)
        _view_agg_rows = []
        for e in eqs:
            for s in all_services:
                sid = s.get("id")
                sname = s.get("nome", "")
                if not sid:
                    continue
                t = task_map.get((e["id"], sid)) or {}
                _td = t.get("dt_etapa_d")
                _tr = t.get("dt_etapa_r")
                _tm = t.get("dt_etapa_m")

                def _hrs(a, b):
                    try:
                        ta = pd.to_datetime(a, utc=True)
                        tb = pd.to_datetime(b, utc=True)
                        return round(
                            (tb - ta).total_seconds() / 3600,
                            1) if pd.notna(ta) and pd.notna(tb) else None
                    except BaseException:
                        return None
                _view_agg_rows.append(
                    {
                        "Frota": eq_label.get(
                            e["id"], str(
                                e.get(
                                    "id", ""))), "Serviço": sname, "D→R (h)": _hrs(
                            _td, _tr), "R→M (h)": _hrs(
                            _tr, _tm), "D→M (h)": _hrs(
                                _td, _tm), })
        view_agg = pd.DataFrame(
            _view_agg_rows) if _view_agg_rows else pd.DataFrame()

        # ── TAB: RESUMO ──
        with tab_resumo:
            render_summary_tab(resumo_df=resumo_df)

        # ── TAB: MATRIZ ──
        with tab_matriz:
            from .matrix_tab import render_matrix_tab
            render_matrix_tab(
                sb=sb,
                revisao_id=revisao_id,
                grupo_id=grupo_id,
                group_atraso_dias=group_atraso_dias,
                semanas_disp=semanas_disp,
                semana_sugerida=_semana_sugerida,
                group_rev_start=group_rev_start,
                setor_to_services=setor_to_services,
                tarefas=tarefas,
                eqs=eqs,
                task_map=task_map,
                eq_label_short=eq_label_short,
            )

        # ── TAB: EVOLUÇÃO SEMANAL ──
        with tab_evolucao:
            st.markdown("### Evolução semanal")
            st.caption(
                "Acompanhe o ritmo de conclusão semana a semana versus a meta linear.")

            col_evo1, col_evo2 = st.columns([1, 2])
            with col_evo1:
                rank_mode = st.radio("Escopo:",
                                     ["Grupo inteiro",
                                      "Setor específico"],
                                     horizontal=False,
                                     key=f"evo_mode_{revisao_id}_{grupo_id}")
            setor_sel_rank = None
            with col_evo2:
                if rank_mode == "Setor específico":
                    setores_rank = sorted(
                        setor_to_services.keys(), key=lambda x: x.lower())
                    # FIX #7: persistir setor selecionado entre reruns
                    _evo_setor_key = f"evo_setor_val_{grupo_id}"
                    _evo_default = st.session_state.get(
                        _evo_setor_key, setores_rank[0] if setores_rank else None)
                    _evo_idx = setores_rank.index(
                        _evo_default) if _evo_default in setores_rank else 0
                    setor_sel_rank = st.selectbox(
                        "Setor",
                        setores_rank,
                        index=_evo_idx,
                        key=f"evo_setor_{revisao_id}_{grupo_id}")
                    st.session_state[_evo_setor_key] = setor_sel_rank
                else:
                    st.caption(
                        f"Analisando **{
                            len(eqs)} equipamentos** · **{
                            len(all_services)} serviços** · **{
                            len(all_services) *
                            3 *
                            len(eqs)} etapas** no total")

            chosen = all_services if rank_mode == "Grupo inteiro" else sorted(
                setor_to_services.get(
                    setor_sel_rank, []), key=lambda x: (
                    x.get("nome") or "").lower())
            seen_e = set()
            svc_ids_rank = []
            for s in chosen:
                sid = s.get("id")
                if sid and sid not in seen_e:
                    seen_e.add(sid)
                    svc_ids_rank.append(sid)
            total_cells_rank = int(len(eqs) * max(len(svc_ids_rank), 1) * 3)

            rev_start2 = pd.to_datetime((rev_row or {}).get("data_inicio") or (
                rev_row or {}).get("created_at"), errors="coerce", utc=True)
            if pd.isna(rev_start2):
                rev_start2 = pd.Timestamp(_now_utc()).normalize()

            if tarefas:
                import json as _json
                evo = _build_evo_chart_data(
                    tarefas_json=_json.dumps(tarefas, default=str),
                    svc_ids_rank=tuple(svc_ids_rank),
                    total_cells_rank=total_cells_rank,
                    semanas_total=int((rev_row or {}).get("semanas_total") or 1),
                    rev_start_iso=str(rev_start2),
                )

                if evo is None:
                    st.info("Sem tarefas para esta revisão/grupo.")
                elif evo.get("mode") == "no_timestamps":
                    st.info("Ainda não há timestamps suficientes para gerar o gráfico.")
                elif evo.get("mode") == "semana_col":
                    st.line_chart(
                        pd.DataFrame(evo["cum_vals"]).set_index("Semana"))
                elif evo.get("mode") == "timestamps":
                    pc   = evo["pc"]
                    ps   = evo["ps"]
                    meta = evo["meta"]
                    agg  = evo["agg"]
                    cum  = evo["cum"]
                    idx  = pc.index

                    pct_atual    = float(pc["Cumulativo (%)"].iloc[-1]) if not pc.empty else 0
                    sem_atual    = int(pc.index[-1]) if not pc.empty else 0
                    meta_atual   = float(meta.iloc[-1]) if len(meta) > 0 else 0
                    delta_vs_meta = round(pct_atual - meta_atual, 1)

                    mk1, mk2, mk3, mk4 = st.columns(4)
                    mk1.metric("Progresso atual", f"{pct_atual:.1f}%")
                    mk2.metric("Meta (semana atual)",
                               f"{meta_atual:.1f}%",
                               delta=f"{delta_vs_meta:+.1f}%",
                               delta_color="normal" if delta_vs_meta >= 0 else "inverse")
                    mk3.metric("Semanas decorridas", str(sem_atual))
                    mk4.metric("Total etapas",
                               f"{int(cum.iloc[-1])}/{total_cells_rank}")
                    st.divider()

                    _render_altair_evo(pc, ps, meta)

                    with st.expander("📋 Tabela detalhada", expanded=False):
                        det = pc.join(ps).join(meta).copy()
                        det["Concluídos (semana)"] = agg.reindex(
                            idx).fillna(0).astype(int).values
                        det["Concluídos (acum.)"] = cum.reindex(
                            idx).ffill().fillna(0).astype(int).values
                        st.dataframe(
                            det.reset_index(names="Semana"),
                            use_container_width=True,
                            hide_index=True)
                else:
                    st.info("Sem timestamps nem coluna semana disponíveis.")
            else:
                st.info("Sem tarefas para esta revisão/grupo.")

            # ── M: Comparativo entre revisões ──────────────────────────────
            st.divider()
            st.markdown("#### Comparativo entre revisões do mesmo grupo")
            st.caption("Compare o progresso desta revisão com revisões anteriores do mesmo grupo.")
            try:
                _all_revs = [r for r in revisoes if r.get("id")]
                if len(_all_revs) > 1:
                    # Busca KPIs de todas as revisões para este grupo
                    _comp_rows = []
                    for _rev in _all_revs[:6]:  # max 6 revisões
                        _rid = _rev.get("id")
                        _rtit = _rev.get("titulo") or str(_rid)[:8]
                        try:
                            _trows = (
                                sb.table("tarefas_servico")
                                .select("etapa_d,etapa_r,etapa_m")
                                .eq("tenant_id", tenant_id)
                                .eq("revisao_id", _rid)
                                .in_("equipamento_id", [e["id"] for e in eqs])
                                .execute().data
                            ) or []
                            _done = sum(
                                int(bool(t.get("etapa_d"))) +
                                int(bool(t.get("etapa_r"))) +
                                int(bool(t.get("etapa_m")))
                                for t in _trows
                            )
                            _total = max(len(eqs) * len(all_services) * 3, 1)
                            _pct = round((_done / _total) * 100)
                            _status = _rev.get("status") or "?"
                            _comp_rows.append({
                                "Revisão": _rtit,
                                "Status": _status,
                                "% Concluído": _pct,
                                "Etapas concluídas": _done,
                                "Total esperado": _total,
                            })
                        except Exception:
                            pass  # ignorado — revisão pode não ter dados

                    if _comp_rows:
                        _comp_df = pd.DataFrame(_comp_rows)
                        # Destaca revisão atual
                        _comp_df["Atual"] = _comp_df.apply(
                            lambda r: "◄ atual" if r["Revisão"] in (titulo, revisao_id) else "", axis=1
                        )
                        st.dataframe(
                            _comp_df,
                            use_container_width=True,
                            hide_index=True,
                            column_config={
                                "% Concluído": st.column_config.ProgressColumn(
                                    "% Concluído", min_value=0, max_value=100
                                ),
                            }
                        )
                else:
                    st.info("Apenas uma revisão encontrada — sem dados para comparar.")
            except Exception as _comp_err:
                st.caption(f"Comparativo não disponível: {_comp_err}")


        # ── TAB: ANALYTICS & AUTOMAÇÃO ──
        with tab_analytics:
            st.markdown("### Gestão e automação")
            st.caption("Indicadores executivos, riscos do grupo e atalhos operacionais seguros.")

            a1, a2, a3, a4 = st.columns(4)
            a1.metric("Progresso geral", f"{progresso_atual_pct:.0f}%")
            a2.metric(
                "Meta esperada",
                f"{expected_pct_now:.1f}%",
                delta=f"{delta_vs_expected_now:+.1f}%",
                delta_color="normal" if delta_vs_expected_now >= 0 else "inverse",
            )
            a3.metric("Setores risco alto", sum(1 for item in analytics_sector_intelligence if item.get("risk") == "alto"))
            a4.metric("Equip. críticos", critical_eq_count)

            for insight in automation_insights[:5]:
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
                if st.button("Abrir setores críticos", key=f"mtz_auto_open_high_{grupo_id}", use_container_width=True):
                    opened = 0
                    for item in analytics_priority_sorted:
                        if item.get("risk") == "alto":
                            _sector_set_open(revisao_id, grupo_id, str(item.get("setor_nome")), True)
                            opened += 1
                    if opened:
                        st.toast(f"{opened} setor(es) críticos preparados na aba Matriz.")
                    else:
                        st.toast("Nenhum setor crítico para abrir.")
                    st.rerun()
            with b2:
                if st.button("Abrir top 3 prioridades", key=f"mtz_auto_open_top3_{grupo_id}", use_container_width=True):
                    opened = 0
                    for item in analytics_priority_sorted[:3]:
                        _sector_set_open(revisao_id, grupo_id, str(item.get("setor_nome")), True)
                        opened += 1
                    if opened:
                        st.toast(f"Top {opened} prioridades preparadas na aba Matriz.")
                    st.rerun()
            with b3:
                if st.button("Fechar setores sob controle", key=f"mtz_auto_close_low_{grupo_id}", use_container_width=True):
                    closed = 0
                    for item in analytics_sector_intelligence:
                        if item.get("risk") == "baixo":
                            _sector_set_open(revisao_id, grupo_id, str(item.get("setor_nome")), False)
                            closed += 1
                    if closed:
                        st.toast(f"{closed} setor(es) sob controle fechados.")
                    st.rerun()
            st.markdown("#### Equipamentos que exigem atenção")
            if resumo_df.empty:
                st.info("Sem dados de equipamentos para análise.")
            else:
                critical_equipment_df = resumo_df.copy()
                critical_equipment_df["Risco"] = critical_equipment_df["%"].apply(
                    lambda v: "alto" if int(v) < 50 else ("medio" if int(v) < 80 else "baixo")
                )
                critical_equipment_df = critical_equipment_df.sort_values(
                    by=["%", "Concluidos"],
                    ascending=[True, True],
                ).head(10)[["Equipamento", "%", "Concluidos", "Total", "Risco"]]
                st.dataframe(
                    critical_equipment_df,
                    use_container_width=True,
                    hide_index=True,
                )

            st.markdown("#### Lead time médio entre etapas")
            if view_agg.empty:
                st.info("Sem dados de tempo suficientes para calcular lead time.")
            else:
                lt_dr = pd.to_numeric(view_agg.get("D→R (h)"), errors="coerce")
                lt_rm = pd.to_numeric(view_agg.get("R→M (h)"), errors="coerce")
                lt_dm = pd.to_numeric(view_agg.get("D→M (h)"), errors="coerce")
                l1, l2, l3 = st.columns(3)
                l1.metric("Mediana D→R", _fmt_duration_from_hours(lt_dr.dropna().median() if lt_dr is not None and not lt_dr.dropna().empty else None))
                l2.metric("Mediana R→M", _fmt_duration_from_hours(lt_rm.dropna().median() if lt_rm is not None and not lt_rm.dropna().empty else None))
                l3.metric("Mediana D→M", _fmt_duration_from_hours(lt_dm.dropna().median() if lt_dm is not None and not lt_dm.dropna().empty else None))

        # ── TAB: TEMPOS ──
        with tab_tempos:
            from .tempos_tab import render_tempos_tab
            render_tempos_tab(
                sb=sb,
                tenant_id=tenant_id,
                revisao_id=revisao_id,
                eq_ids=eq_ids,
                tarefas=tarefas,
                svc_ids_rank=svc_ids_rank if svc_ids_rank else [],
                svc_ids_all=svc_ids_all,
                all_services=all_services,
                eq_label_short=eq_label_short,
                eq_label=eq_label,
            )

        # ── TAB: EDITAR CÉLULA ──
        if tab_editor is not None:
            with tab_editor:
                from .editor_tab import render_editor_tab, render_bulk_editor
                _edit_mode = st.radio(
                    "Modo de edição",
                    ["✏️ Célula individual", "⚡ Lote por serviço"],
                    horizontal=True,
                    key="mat_edit_mode",
                )
                st.divider()
                if _edit_mode == "✏️ Célula individual":
                    render_editor_tab(
                        sb=sb,
                        tenant_id=tenant_id,
                        revisao_id=revisao_id,
                        grupo_id=grupo_id,
                        setor_to_services=setor_to_services,
                        eq_label_short=eq_label_short,
                        task_map=task_map,
                        semana_sugerida=_semana_sugerida,
                        eq_ocultos_set=_eq_ocultos_set,
                    )
                else:
                    render_bulk_editor(
                        sb=sb,
                        tenant_id=tenant_id,
                        revisao_id=revisao_id,
                        setor_to_services=setor_to_services,
                        task_map=task_map,
                        eqs=eqs,
                        eq_label_short=eq_label_short,
                        semana_sugerida=_semana_sugerida,
                    )

        # ── TAB: EXPORTAR ──
        with tab_exportar:
            from .export_tab import render_export_tab
            render_export_tab(
                tenant_id=tenant_id,
                grupo_id=grupo_id,
                revisao_id=revisao_id,
                titulo=titulo,
                grupo_nome=grupo_nome,
                resumo_df=resumo_df,
                view_agg=view_agg,
                sector_tables_for_export=sector_tables_for_export,
                data_version=st.session_state.get("data_version", "0"),
            )

    except Exception as e:
        st.error("Erro ao renderizar a Matriz.")
        st.exception(e)
