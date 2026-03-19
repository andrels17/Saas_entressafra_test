"""Renderização principal da Matriz Operacional (módulo modularizado)."""
from __future__ import annotations

import time
from datetime import date, datetime, timezone

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

try:
    from src.ui.pages.matriz_legacy_full import _pct_bar_html
except Exception:
    def _pct_bar_html(pct, height=4):
        try:
            pct = max(0.0, min(100.0, float(pct or 0)))
        except Exception:
            pct = 0.0
        return f"<div style=\"width:100%;height:{int(height)}px;background:rgba(255,255,255,.08);border-radius:999px;overflow:hidden;\"><div style=\"width:{pct:.1f}%;height:100%;background:linear-gradient(90deg,#22c55e,#eab308,#ef4444);\"></div></div>"



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

def render_matriz():
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

        eq_ids = [e["id"] for e in eqs]
        # eq_label: descricao completa — Resumo e PDF
        eq_label = {
            e["id"]: f"{
                e.get(
                    'frota',
                    '')} — {
                e.get('modelo') or ''}".strip(" —") for e in eqs}
        # eq_label_short: apenas o numero/frota — Matriz, Tempos, Editor
        eq_label_short = {e["id"]: (str(e.get("frota") or "")).strip() or str(
            e.get("id", "")) for e in eqs}
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
            pass
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
            st.markdown("### Drill-down por setor")
            st.caption(
                "Marque as etapas (D/R/M) direto na tabela. Setores 🔴 são prioridade — expanda para editar.")
            atraso_dias = group_atraso_dias
            fc1, fc2, fc3 = st.columns([1, 1.5, 1.5])
            with fc1:
                atraso_dias = st.number_input(
                    "Atraso (dias)",
                    min_value=1,
                    max_value=90,
                    value=atraso_dias,
                    step=1,
                    key="mtz_atraso_in",
                    help="Marca coluna M como atraso quando passou mais de X dias.")
                st.session_state["matriz_atraso_dias"] = int(atraso_dias)
            with fc2:
                # Melhoria 4: filtro de semana
                sem_opts = ["Todas as semanas"] + \
                    [f"Semana {s}" for s in semanas_disp]
                sem_pick = st.selectbox(
                    "Filtrar por semana",
                    sem_opts,
                    index=0,
                    key="mtz_sem_pick")
                semana_filtro = None if sem_pick == "Todas as semanas" else int(
                    sem_pick.split()[-1])
            with fc3:
                semana_lote = st.number_input(
                    "📅 Semana do apontamento",
                    min_value=0, max_value=99,
                    value=int(_semana_sugerida),
                    step=1, key="mtz_semana_lote",
                    help=f"Semana sugerida automaticamente ({_semana_sugerida}) com base na data de início da revisão. "
                    "Altere se estiver registrando uma etapa de outra semana. "
                    "Aplicada apenas em tarefas que ainda não têm semana definida."
                )

            rev_start = group_rev_start

            # FIX #6: chips clicáveis — se o usuário clicou num chip, pular
            # direto para aquele setor
            _chip_target = st.session_state.pop("mtz_chip_jump", None)

            sector_intelligence = []
            for _setor_nome in sorted(setor_to_services.keys(), key=lambda x: x.lower()):
                _svs_all = sorted(setor_to_services[_setor_nome], key=lambda x: (x.get("nome") or "").lower())
                _svc_ids_all = [s["id"] for s in _svs_all if s.get("id")]
                if semana_filtro is not None:
                    _svc_na_sem = {t["servico_id"] for t in tarefas if t.get("semana") == semana_filtro and t.get("servico_id")}
                    _svc_ids_all = [sid for sid in _svc_ids_all if sid in _svc_na_sem]
                if not _svc_ids_all:
                    continue
                _intel = summarize_sector_intelligence(
                    equipamentos=eqs,
                    svc_ids=_svc_ids_all,
                    task_map=task_map,
                    atraso_dias=int(atraso_dias),
                    rev_start=rev_start,
                )
                _intel["setor_nome"] = _setor_nome
                sector_intelligence.append(_intel)

            if sector_intelligence:
                _priority_sorted = sorted(
                    sector_intelligence,
                    key=_sector_priority_sort_key,
                )
                st.markdown('<div class="mtz-priority-panel">', unsafe_allow_html=True)
                st.markdown("#### 🔥 Prioridades agora")
                for _idx, _item in enumerate(_priority_sorted[:3], start=1):
                    st.markdown(
                        f'<div class="mtz-priority-item"><b>{_idx}. {_item["setor_nome"]}</b> · '
                        f'{_item["risk_icon"]} risco {_item["risk_label"]} · '
                        f'<b>{_item["pct"]}%</b> concluído · '
                        f'{_item["criticos"]} críticos · '
                        f'{_item["atrasadas_m"]} atraso(s) de montagem<br>'
                        f'<span style="opacity:.78">{_item["recommendation"]}</span></div>',
                        unsafe_allow_html=True,
                    )
                st.markdown('</div>', unsafe_allow_html=True)

            for setor_nome in sorted(
                    setor_to_services.keys(),
                    key=lambda x: x.lower()):
                svs = sorted(
                    setor_to_services[setor_nome],
                    key=lambda x: (
                        x.get("nome") or "").lower())
                svc_ids = [s["id"] for s in svs if s.get("id")]
                svc_names = [s.get("nome") or str(s.get("id"))
                             for s in svs if s.get("id")]
                if not svc_ids:
                    continue
                if semana_filtro is not None:
                    svc_na_sem = {t["servico_id"] for t in tarefas if t.get(
                        "semana") == semana_filtro and t.get("servico_id")}
                    svc_ids_v = [sid for sid in svc_ids if sid in svc_na_sem]
                    svc_names_v = [
                        svc_names[i] for i,
                        sid in enumerate(svc_ids) if sid in svc_na_sem]
                    if not svc_ids_v:
                        continue
                else:
                    svc_ids_v = svc_ids
                    svc_names_v = svc_names

                # Pré-calcular progresso para label e auto-expand
                _done_s, _tot_s, _pct_s, _lbl_exp = sector_progress_label(
                    equipamentos=eqs,
                    svc_ids=svc_ids_v,
                    task_map=task_map,
                    setor_nome=setor_nome,
                )

                # Lazy load real por setor: só renderiza o conteúdo quando aberto
                _auto_expand = (_pct_s == 0) or (setor_nome == _chip_target)
                if _auto_expand and not _sector_is_open(revisao_id, grupo_id, setor_nome):
                    _sector_set_open(revisao_id, grupo_id, setor_nome, True)

                _sector_open = _sector_is_open(revisao_id, grupo_id, setor_nome)
                _sector_intel = summarize_sector_intelligence(
                    equipamentos=eqs,
                    svc_ids=svc_ids_v,
                    task_map=task_map,
                    atraso_dias=int(atraso_dias),
                    rev_start=rev_start,
                )
                _risk_class = "high" if _sector_intel["risk"] == "alto" else ("medium" if _sector_intel["risk"] == "medio" else "low")

                st.markdown(f'<div class="mtz-sector-box {_risk_class}">', unsafe_allow_html=True)
                with st.container():
                    _head_l, _head_r = st.columns([0.78, 0.22])
                    with _head_l:
                        st.markdown(f"#### {_lbl_exp}")
                        st.markdown(
                            '<div class="mtz-risk-badges">'
                            f'<span class="mtz-risk-badge {_risk_class}">{_sector_intel["risk_icon"]} Risco {_sector_intel["risk_label"]}</span>'
                            f'<span class="mtz-risk-badge {"high" if _sector_intel["criticos"] else "low"}">Críticos: {_sector_intel["criticos"]}</span>'
                            f'<span class="mtz-risk-badge {"medium" if _sector_intel["em_andamento"] else "low"}">Em andamento: {_sector_intel["em_andamento"]}</span>'
                            f'<span class="mtz-risk-badge {"high" if _sector_intel["atrasadas_m"] else "low"}">Atraso M: {_sector_intel["atrasadas_m"]}</span>'
                            f'<span class="mtz-risk-badge {"medium" if _sector_intel["sem_inicio"] else "low"}">Sem início: {_sector_intel["sem_inicio"]}</span>'
                            '</div>',
                            unsafe_allow_html=True,
                        )
                        st.caption(_sector_intel["recommendation"])
                    with _head_r:
                        _toggle_label = "Ocultar setor" if _sector_open else "Abrir setor"
                        if st.button(
                                _toggle_label,
                            key=f"mtz_toggle_sector_{revisao_id}_{grupo_id}_{setor_nome}".replace(" ", "_"),
                            use_container_width=True,
                        ):
                            _sector_set_open(revisao_id, grupo_id, setor_nome, not _sector_open)
                            st.rerun()

                    if not _sector_open:
                        st.caption("Clique em **Abrir setor** para carregar a grade e editar apenas este setor.")
                        continue
                    df, col_meta, obs_map = build_sector_frame(
                        equipamentos=eqs,
                        svc_ids=svc_ids_v,
                        svc_names=svc_names_v,
                        task_map=task_map,
                        eq_label_short=eq_label_short,
                    )
                    if df.empty:
                        st.info("Sem dados para este setor.")
                        continue
                    df_display = df.set_index("_equip_id", drop=True)
                    svc_bool = [
                        c for c in df_display.columns if c not in (
                            "%", "Equipamento")]
                    tok_s, tc_s, pg, pm, eq_100s = sector_summary_metrics(df_display, svc_bool)
                    c1s, c2s, c3s = st.columns([1, 1, 2])
                    c1s.metric("Geral (ponderado)", f"{pg}%")
                    c2s.metric("Médio (frotas)", f"{pm}%")
                    with c3s:
                        _eq100_html = (
                            f' &nbsp;·&nbsp; <b style="color:#12B76A">{eq_100s}</b> 100%' if eq_100s > 0 else "")
                        st.markdown(
                            f'<div style="padding-top:8px;font-size:.82rem;color:rgba(255,255,255,.65)">'
                            f'{len(df)} eq &nbsp;·&nbsp; {len(svc_ids_v)} serviços &nbsp;·&nbsp; '
                            f'<b style="color:rgba(255,255,255,.9)">{tok_s}/{tc_s}</b> concluídas'
                            f'{_eq100_html}'
                            f'</div>'
                            f'{_pct_bar_html(pg, height=4)}',
                            unsafe_allow_html=True)

                    # Removida coluna "%" — o progresso já aparece no header
                    # acima; mantemos só "Equipamento"
                    df_display = df_display.drop(
                        columns=["%"], errors="ignore")
                    if "Status" not in df_display.columns:
                        df_display.insert(
                            0,
                            "Status",
                            df_display.apply(
                                lambda rw: "✓" if all(
                                    bool(
                                        rw.get(
                                            c,
                                            False)) for c in svc_bool) else "",
                                axis=1)) if svc_bool else None

                    # Observacoes com frota curta
                    if obs_map:
                        with st.expander(f"💬 Observações ({len(obs_map)})", expanded=False):
                            for key, obs_txt in obs_map.items():
                                eid_k, sid_k = key.split("__")
                                eq_n = eq_label_short.get(eid_k, eid_k)
                                _svc_names = _svc_name_map(svs)
                                svc_n = _svc_names.get(str(sid_k), sid_k)
                                st.markdown(
                                    f"**Frota {eq_n}** · {svc_n}: _{obs_txt}_")

                    _svc_names = _svc_name_map(svs)
                    kb = f"mat_ed_{revisao_id}_{grupo_id}_{setor_nome}".replace(
                        " ", "_")
                    mode_options = ["Editar", "Visual"] if can_edit else ["Visual"]
                    mode = st.radio(
                        "Visualização", mode_options, horizontal=True, key=f"mtz_mode_{kb}")

                    if mode == "Visual":
                        days_since = int(
                            (pd.Timestamp(
                                _now_utc()) -
                                rev_start).days) if isinstance(
                            rev_start,
                            pd.Timestamp) else 0
                        df_vis = df_display.copy()
                        for c in svc_bool:
                            df_vis[c] = df_vis[c].apply(
                                lambda v: "OK" if bool(v) else "")
                        if days_since > atraso_dias:
                            for c in [
                                    c for c in svc_bool if str(c).strip().endswith(" M")]:
                                df_vis.loc[df_vis[c] == "", c] = "!"
                        st.dataframe(
                            df_vis.style.apply(
                                _style_heatmap,
                                axis=None),
                            use_container_width=True,
                            hide_index=True)
                        edited = None
                    else:
                        edited = st.data_editor(
                            df_display, key=kb, use_container_width=True, hide_index=True, column_config={
                                "Status": st.column_config.TextColumn(
                                    "✓", disabled=True, width="small"), "Equipamento": st.column_config.TextColumn(
                                    "Equipamento", disabled=True), **{
                                    col: st.column_config.CheckboxColumn(col) for col in svc_bool}}, disabled=[
                                "Status", "Equipamento"])

                    sv1, sv2, _ = st.columns([1.2, 1.8, 1])
                    with sv1:
                        if can_edit:
                            save_now = form_submit_button(
                                "💾 Salvar alterações",
                                key=f"save_{kb}",
                                help="Valida e prepara as alterações feitas no grid deste setor antes da confirmação final.",
                            )
                        else:
                            st.button(
                                "💾 Salvar alterações",
                                key=f"save_{kb}_disabled",
                                disabled=True,
                                use_container_width=True,
                                help="Somente administradores e supervisores podem editar a matriz.",
                            )
                            save_now = False
                    with sv2:
                        st.caption(
                            "Marque/desmarque etapas acima e clique em Salvar." if can_edit else "Somente administradores e supervisores podem editar a matriz.")

                    _pending_changes_key = f"pending_changes_{kb}"
                    _pending_preview_key = f"pending_preview_{kb}"
                    _field_lbl = {
                        "etapa_d": "D",
                        "etapa_r": "R",
                        "etapa_m": "M"}

                    if save_now:
                        if edited is None:
                            st.warning(
                                "Troque para o modo **Editar** para poder salvar alterações.")
                        else:
                            changes = _collect_matrix_changes(
                                df_display=df_display,
                                edited=edited,
                                svc_bool=svc_bool,
                                col_meta=col_meta,
                            )
                            if not changes:
                                st.session_state.pop(
                                    _pending_changes_key, None)
                                st.session_state.pop(
                                    _pending_preview_key, None)
                                st.info(
                                    "Nenhuma alteração detectada — faça alguma marcação antes de salvar.")
                            else:
                                _prev_lines = build_change_preview_lines(
                                    changes,
                                    eq_label_short=eq_label_short,
                                    svc_names=_svc_names,
                                    field_labels=_field_lbl,
                                    limit=8,
                                )
                                st.session_state[_pending_changes_key] = changes
                                st.session_state[_pending_preview_key] = _prev_lines
                                st.rerun()

                    pending_changes = st.session_state.get(
                        _pending_changes_key) or []
                    pending_preview = st.session_state.get(
                        _pending_preview_key) or []
                    if pending_changes:
                        with st.container(border=True):
                            st.markdown(
                                f"**{len(pending_changes)} alteração(ões) a salvar:**")
                            st.markdown("\n".join(pending_preview))
                            c_yes, c_no, _ = st.columns([1, 1, 2])
                            with c_yes:
                                confirm_now = st.button(
                                    "✅ Confirmar", key=f"yes_{kb}", type="primary", use_container_width=True)
                            with c_no:
                                cancel_now = st.button(
                                    "✖ Cancelar", key=f"no_{kb}", use_container_width=True)

                        if cancel_now:
                            st.session_state.pop(_pending_changes_key, None)
                            st.session_state.pop(_pending_preview_key, None)
                            st.rerun()

                        if confirm_now:
                            now_iso = datetime.now(timezone.utc).isoformat()
                            ok = 0
                            failed = 0
                            upsert_rows = []

                            for eid, sid, field, nv in pending_changes:
                                # Tenta achar tarefa existente no task_map
                                t = (
                                    task_map.get((str(eid), str(sid)))
                                    or task_map.get((eid, sid))
                                    or {}
                                )
                                tid = t.get("id")

                                if tid:
                                    # Tarefa existe: monta update normal
                                    upd = {
                                        "id": tid,
                                        field: bool(nv),
                                        "updated_by": current_user_id() or None,
                                    }
                                    dtf = {
                                        "etapa_d": "dt_etapa_d",
                                        "etapa_r": "dt_etapa_r",
                                        "etapa_m": "dt_etapa_m",
                                    }.get(field)
                                    if dtf:
                                        upd[dtf] = now_iso if nv else None
                                    if nv and not t.get("semana") and int(semana_lote) > 0:
                                        upd["semana"] = int(semana_lote)
                                    upsert_rows.append(upd)
                                else:
                                    # Tarefa não existe: cria com todos os campos NOT NULL explícitos
                                    # Coleta todas as mudanças deste mesmo par (eid, sid) de uma vez
                                    _pair_fields = {
                                        f2: bool(nv2)
                                        for eid2, sid2, f2, nv2 in pending_changes
                                        if str(eid2) == str(eid) and str(sid2) == str(sid)
                                    }
                                    row = {
                                        "tenant_id": tenant_id,
                                        "revisao_id": revisao_id,
                                        "equipamento_id": eid,
                                        "servico_id": sid,
                                        "etapa_d": bool(_pair_fields.get("etapa_d", False)),
                                        "etapa_r": bool(_pair_fields.get("etapa_r", False)),
                                        "etapa_m": bool(_pair_fields.get("etapa_m", False)),
                                        "status": "pendente",
                                        "updated_by": current_user_id() or None,
                                    }
                                    if int(semana_lote) > 0:
                                        row["semana"] = int(semana_lote)
                                    try:
                                        sb.table("tarefas_servico").insert(row).execute()
                                        ok += 1
                                    except Exception as _ins_err:
                                        # Tarefa já existe mas não estava no cache — atualiza por chave natural
                                        try:
                                            sb.table("tarefas_servico").update({
                                                field: bool(nv),
                                                "updated_by": current_user_id() or None,
                                            }).eq("tenant_id", tenant_id).eq(
                                                "revisao_id", revisao_id
                                            ).eq("equipamento_id", eid).eq(
                                                "servico_id", sid
                                            ).execute()
                                            ok += 1
                                        except Exception as _upd_err:
                                            st.warning(f"Falha ao salvar eq={eid} svc={sid}: {_upd_err}")
                                            failed += 1

                            st.session_state.pop(_pending_changes_key, None)
                            st.session_state.pop(_pending_preview_key, None)

                            # Aplica updates em lote para tarefas existentes
                            if upsert_rows:
                                _ok, _fail = _bulk_update_tasks(sb, upsert_rows)
                                ok += _ok
                                failed += _fail

                            if ok > 0 or failed == 0:
                                st.success(
                                    f"✅ {ok} etapas salvas"
                                    + (f"  ·  {failed} falharam" if failed else "")
                                )
                                st.toast("✅ Alterações aplicadas com sucesso!")
                                bump_data_version()
                                try:
                                    _load_payload.clear()
                                except Exception:
                                    pass
                                try:
                                    _group_kpis.clear()
                                except Exception:
                                    pass
                                st.session_state.pop("_mtz_payload_cache", None)
                                try:
                                    nav.rerun_keep_menu()
                                except Exception:
                                    st.rerun()
                            else:
                                st.error(f"❌ Todas as {failed} alterações falharam.")

                    exp_df = df_display.reset_index(drop=True).copy()
                    for c in [
                        c for c in exp_df.columns if c not in (
                            "%", "Equipamento", "Status")]:
                        exp_df[c] = exp_df[c].apply(
                            lambda v: "OK" if bool(v) else "")
                    # sector_tables_for_export já foi pré-populado antes das
                    # tabs (fix #3)
                    st.markdown("</div>", unsafe_allow_html=True)

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
            df_tasks = pd.DataFrame(tarefas)
            if not df_tasks.empty:
                has_dt = any(
                    (c in df_tasks.columns and df_tasks[c].notna().any()) for c in [
                        "dt_etapa_d", "dt_etapa_r", "dt_etapa_m"])
                if has_dt:
                    def _wk(s):
                        dt = pd.to_datetime(s, errors="coerce", utc=True)
                        return ((dt - rev_start2).dt.days.clip(lower=0) //
                                7 + 1).astype("Int64")
                    events = []
                    for dc in ["dt_etapa_d", "dt_etapa_r", "dt_etapa_m"]:
                        if dc not in df_tasks.columns:
                            continue
                        sub = df_tasks[df_tasks["servico_id"].isin(
                            svc_ids_rank)].copy()
                        if sub.empty:
                            continue
                        sub["wk"] = _wk(sub[dc])
                        sub = sub.dropna(subset=["wk"])
                        if not sub.empty:
                            events.append(sub[["wk"]].assign(cnt=1))
                    if events:
                        ev = pd.concat(events, ignore_index=True)
                        agg = ev.groupby("wk", dropna=True)[
                            "cnt"].sum().sort_index()
                        cum = agg.cumsum()
                        mw = int(max(cum.index.max(), agg.index.max()))
                        idx = range(1, mw + 1)
                        pc = (cum / max(total_cells_rank, 1) *
                              100).round(1).to_frame("Cumulativo (%)")
                        ps = (agg / max(total_cells_rank, 1) *
                              100).round(1).to_frame("Na semana (%)")
                        pc = pc.reindex(idx).ffill().fillna(0)
                        ps = ps.reindex(idx).fillna(0)
                        wt = int(
                            (rev_row or {}).get("semanas_total") or mw or 1)
                        meta = pd.Series([min(100.0, (w / max(wt, 1)) * 100)
                                         for w in idx], index=idx, name="Meta (%)")
                        # KPIs de evolução
                        pct_atual = float(
                            pc["Cumulativo (%)"].iloc[-1]) if not pc.empty else 0
                        sem_atual = int(pc.index[-1]) if not pc.empty else 0
                        meta_atual = float(
                            meta.iloc[-1]) if len(meta) > 0 else 0
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
                        st.line_chart(pc.join(ps).join(meta))
                        with st.expander("📋 Tabela detalhada", expanded=False):
                            det = pc.join(ps).join(meta).copy()
                            det["Concluídos (semana)"] = agg.reindex(
                                idx).fillna(0).astype(int).values
                            det["Concluídos (acum.)"] = cum.reindex(
                                idx).ffill().fillna(0).astype(int).values
                            st.dataframe(
                                det.reset_index(
                                    names="Semana"),
                                use_container_width=True,
                                hide_index=True)
                    else:
                        st.info(
                            "Ainda não há timestamps suficientes para gerar o gráfico.")
                elif "semana" in df_tasks.columns:
                    df_done = df_tasks[(df_tasks["servico_id"].isin(
                        svc_ids_rank)) & df_tasks["semana"].notna()].copy()
                    if not df_done.empty:
                        df_done["semana"] = pd.to_numeric(
                            df_done["semana"], errors="coerce").astype("Int64")
                        df_done = df_done.dropna(subset=["semana"])
                        cum_vals = []
                        for w in sorted(df_done["semana"].unique()):
                            w_df = df_done[df_done["semana"] <= w]
                            ok_w = int(w_df[["etapa_d", "etapa_r", "etapa_m"]].fillna(
                                False).astype(bool).astype(int).sum().sum())
                            cum_vals.append({"Semana": int(w), "% Concluído": round(
                                (ok_w / max(total_cells_rank, 1)) * 100, 1)})
                        st.line_chart(
                            pd.DataFrame(cum_vals).set_index("Semana"))
                    else:
                        st.info("Sem dados de evolução.")
                else:
                    st.info("Sem timestamps nem coluna semana disponíveis.")
            else:
                st.info("Sem tarefas para esta revisão/grupo.")


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
            st.markdown("### ⏱️ Tempos de execução (D/R/M)")
            st.caption(
                "Análise de duração entre as etapas Desmontagem → Revisão → Montagem.")
            svc_ids_tempos = svc_ids_rank if svc_ids_rank else svc_ids_all
            tempos_rows = []
            try:
                tempos_rows = (
                    sb.table("v_tarefas_etapas_duracoes") .select(
                        "equipamento_id,servico_id,dt_inicio,dt_etapa_d,dt_etapa_r,dt_etapa_m,"
                        "horas_d_para_r,horas_r_para_m,horas_d_para_m,horas_total") .eq(
                        "tenant_id",
                        tenant_id).eq(
                        "revisao_id",
                        revisao_id) .in_(
                        "equipamento_id",
                        eq_ids).execute().data) or []
            except Exception:
                tempos_rows = []
            df_t = pd.DataFrame(
                tempos_rows) if tempos_rows else pd.DataFrame(tarefas)
            if not tempos_rows:
                for col in [
                    "dt_inicio",
                    "dt_etapa_d",
                    "dt_etapa_r",
                        "dt_etapa_m"]:
                    if col not in df_t.columns:
                        df_t[col] = pd.NaT
                    df_t[col] = pd.to_datetime(
                        df_t[col], errors="coerce", utc=True)
                df_t["horas_d_para_r"] = (
                    df_t["dt_etapa_r"] - df_t["dt_etapa_d"]).dt.total_seconds() / 3600
                df_t["horas_r_para_m"] = (
                    df_t["dt_etapa_m"] - df_t["dt_etapa_r"]).dt.total_seconds() / 3600
                df_t["horas_d_para_m"] = (
                    df_t["dt_etapa_m"] - df_t["dt_etapa_d"]).dt.total_seconds() / 3600
                df_t["horas_total"] = (
                    df_t["dt_etapa_m"] - df_t["dt_inicio"]).dt.total_seconds() / 3600
            if "servico_id" in df_t.columns:
                df_t = df_t[df_t["servico_id"].isin(svc_ids_tempos)].copy()
            view_agg = pd.DataFrame()
            if not df_t.empty:
                sv_map = {s["id"]: (s.get("nome") or str(s["id"]))
                          for s in all_services if s.get("id")}
                # usar rótulo curto na tabela de tempos
                df_t["Frota"] = df_t["equipamento_id"].map(eq_label_short)
                df_t["Equipamento"] = df_t["equipamento_id"].map(
                    eq_label)  # mantido para export
                df_t["Serviço"] = df_t["servico_id"].map(
                    sv_map).fillna(df_t["servico_id"].astype(str))
                for c in [
                    "horas_d_para_r",
                    "horas_r_para_m",
                    "horas_d_para_m",
                        "horas_total"]:
                    if c in df_t.columns:
                        df_t[c] = pd.to_numeric(df_t[c], errors="coerce")

                # KPIs globais de tempo
                med_total = df_t["horas_total"].dropna().mean(
                ) if "horas_total" in df_t.columns else None
                med_dr = df_t["horas_d_para_r"].dropna().mean(
                ) if "horas_d_para_r" in df_t.columns else None
                med_rm = df_t["horas_r_para_m"].dropna().mean(
                ) if "horas_r_para_m" in df_t.columns else None
                completos_total = int(
                    df_t["horas_total"].notna().sum()) if "horas_total" in df_t.columns else 0
                tk1, tk2, tk3, tk4 = st.columns(4)
                tk1.metric("Itens completos", str(completos_total))
                tk2.metric(
                    "Média total (D→M)",
                    _fmt_duration_from_hours(med_total))
                tk3.metric("Média D→R", _fmt_duration_from_hours(med_dr))
                tk4.metric("Média R→M", _fmt_duration_from_hours(med_rm))
                st.divider()

                t_col1, t_col2 = st.columns([1, 1])
                with t_col1:
                    st.markdown("#### Resumo por frota")
                    agg = (
                        df_t.groupby(
                            "Frota", dropna=False) .agg(
                            itens=(
                                "servico_id", "count"), completos=(
                                "horas_total", lambda s: int(
                                    pd.Series(s).notna().sum())), media_total_h=(
                                "horas_total", "mean"), p90_total_h=(
                                "horas_total", lambda s: float(
                                    pd.Series(s).dropna().quantile(.9)) if pd.Series(s).dropna().shape[0] else None), media_d_r_h=(
                                        "horas_d_para_r", "mean"), media_r_m_h=(
                                            "horas_r_para_m", "mean")) .reset_index())
                    agg["Média Total"] = agg["media_total_h"].apply(
                        _fmt_duration_from_hours)
                    agg["P90"] = agg["p90_total_h"].apply(
                        _fmt_duration_from_hours)
                    agg["D→R"] = agg["media_d_r_h"].apply(
                        _fmt_duration_from_hours)
                    agg["R→M"] = agg["media_r_m_h"].apply(
                        _fmt_duration_from_hours)
                    view_agg_short = agg[["Frota",
                                          "itens",
                                          "completos",
                                          "Média Total",
                                          "P90",
                                          "D→R",
                                          "R→M"]].sort_values(["completos",
                                                               "itens"],
                                                              ascending=[False,
                                                                         False])
                    # view_agg para export ainda usa Equipamento
                    agg2 = agg.copy()
                    agg2["Equipamento"] = agg2["Frota"].map(
                        {v: eq_label.get(k, v) for k, v in eq_label_short.items()})
                    view_agg = agg2[["Equipamento",
                                     "itens",
                                     "completos",
                                     "Média Total",
                                     "P90",
                                     "D→R",
                                     "R→M"]].sort_values(["completos",
                                                          "itens"],
                                                         ascending=[False,
                                                                    False])
                    st.dataframe(view_agg_short.style .set_properties(subset=["Frota"],
                                                                      **{"text-align": "left",
                                                                         "font-weight": "600"}) .set_properties(**{"font-size": "12px"}),
                                 use_container_width=True,
                                 hide_index=True)

                with t_col2:
                    st.markdown("#### Gargalos — Top tempos")
                    metric = st.selectbox(
                        "Ordenar por:", [
                            "Total (D→M)", "D→R", "R→M"], index=0, key="tempo_metric")
                    col_m = {
                        "Total (D→M)": "horas_total",
                        "D→R": "horas_d_para_r",
                        "R→M": "horas_r_para_m"}[metric]
                    top = df_t[["Frota", "Serviço", "horas_d_para_r",
                                "horas_r_para_m", "horas_total"]].copy()
                    top = top.dropna(
                        subset=[col_m]).sort_values(
                        by=[col_m],
                        ascending=False).head(20)
                    top["D→R"] = top["horas_d_para_r"].apply(
                        _fmt_duration_from_hours)
                    top["R→M"] = top["horas_r_para_m"].apply(
                        _fmt_duration_from_hours)
                    top["Total"] = top["horas_total"].apply(
                        _fmt_duration_from_hours)
                    st.dataframe(top[["Frota",
                                      "Serviço",
                                      "D→R",
                                      "R→M",
                                      "Total"]] .style.set_properties(subset=["Frota",
                                                                              "Serviço"],
                                                                      **{"text-align": "left"}) .set_properties(**{"font-size": "12px"}),
                                 use_container_width=True,
                                 hide_index=True)
            else:
                st.info(
                    "Sem dados de tempo ainda. Marque etapas D/R/M com timestamps para começar.")

        # ── TAB: EDITAR CÉLULA ──
        if tab_editor is not None:
            with tab_editor:
                st.markdown("### ✏️ Edição rápida por célula")
                st.caption(
                    "Selecione frota, setor e serviço para atualizar etapas, status e observação.")
    
                # Seletores lado a lado
                ed_c1, ed_c2, ed_c3 = st.columns([1, 1, 1])
                with ed_c1:
                    equip_choices_short = {
                        eq_label_short[eid]: eid for eid in eq_label_short}
                    esl = st.selectbox(
                        "🚜 Frota",
                        list(
                            equip_choices_short.keys()),
                        key="mat_eq_sel")
                    equip_sel = equip_choices_short[esl]
                with ed_c2:
                    setores_ed = sorted(
                        setor_to_services.keys(),
                        key=lambda x: x.lower())
                    if setores_ed:
                        setor_ed = st.selectbox(
                            "📂 Setor", setores_ed, key="mat_setor_sel")
                    else:
                        st.info("Sem setores disponíveis neste grupo.")
                        setor_ed = None
                with ed_c3:
                    if setor_ed:
                        svs_ed = sorted(
                            setor_to_services[setor_ed], key=lambda x: (
                                x.get("nome") or "").lower())
                        svc_choices = {
                            s.get("nome") or str(
                                s.get("id")): s["id"] for s in svs_ed if s.get("id")}
                        if svc_choices:
                            svc_name = st.selectbox("🔧 Serviço", list(
                                svc_choices.keys()), key="mat_srv_sel")
                            svc_sel = svc_choices[svc_name]
                        else:
                            st.info("Sem serviços neste setor.")
                            svc_sel = None
                    else:
                        svc_sel = None
    
                if not setor_ed or not svc_sel:
                    st.info("Selecione um setor e serviço válidos para continuar.")
                else:
                    # Buscar tarefa
                    task_rows_ed = (
                        sb.table("tarefas_servico").select("id,status,semana,observacao,etapa_d,etapa_r,etapa_m") .eq(
                            "tenant_id", tenant_id).eq(
                            "revisao_id", revisao_id) .eq(
                            "equipamento_id", equip_sel).eq(
                            "servico_id", svc_sel).limit(1).execute().data) or []
                    task_ed = task_rows_ed[0] if task_rows_ed else None

                    if not task_ed:
                        st.warning("⚠️ Tarefa não encontrada para esta combinação.")
                    else:
                        st.divider()
                        # Info da tarefa atual em destaque
                        cur_d = bool(task_ed.get("etapa_d"))
                        cur_r = bool(task_ed.get("etapa_r"))
                        cur_m = bool(task_ed.get("etapa_m"))
                        cur_pct = round(
                            ((int(cur_d) + int(cur_r) + int(cur_m)) / 3) * 100)
                        _ed_color = _risk_color(cur_pct)
    
                        def _badge(label, done):
                            if done:
                                return (f'<span style="padding:3px 10px;border-radius:999px;'
                                        f'background:rgba(18,183,106,.2);color:#12B76A;font-size:.8rem">✓ {label}</span>')
                            return (f'<span style="padding:3px 10px;border-radius:999px;'
                                    f'background:rgba(255,255,255,.06);color:rgba(255,255,255,.4);font-size:.8rem">✗ {label}</span>')
    
                        badge_d = _badge("D", cur_d)
                        badge_r = _badge("R", cur_r)
                        badge_m = _badge("M", cur_m)
                        _status_label = "Concluído" if cur_pct == 100 else (
                            "Pendente" if cur_pct == 0 else "Em andamento")
    
                        info_col1, info_col2 = st.columns([2, 1])
                        with info_col1:
                            st.markdown(
                                f'<div style="padding:10px 14px;border-radius:10px;border:1px solid rgba(255,255,255,.1);'
                                f'background:rgba(255,255,255,.04);margin-bottom:8px">'
                                f'<div style="font-size:.8rem;color:rgba(255,255,255,.5);margin-bottom:4px">Estado atual</div>'
                                f'<div style="display:flex;gap:12px;align-items:center">'
                                f'<span style="font-size:.9rem">Frota <b>{esl}</b></span>'
                                f'<span style="color:rgba(255,255,255,.4)">·</span>'
                                f'<span style="font-size:.9rem">{setor_ed}</span>'
                                f'<span style="color:rgba(255,255,255,.4)">·</span>'
                                f'<span style="font-size:.9rem">{svc_name}</span>'
                                f'</div>'
                                f'<div style="margin-top:6px;display:flex;gap:6px">'
                                f'{badge_d}{badge_r}{badge_m}'
                                f'</div></div>',
                                unsafe_allow_html=True)
                        with info_col2:
                            st.metric(
                                "Progresso atual",
                                f"{cur_pct}%",
                                delta=_status_label)
    
                        st.markdown("#### Atualizar etapas")
                        cD, cR, cM, cSem = st.columns([1, 1, 1, 1])
                        with cD:
                            etapa_d = st.checkbox(
                                "✅ Desmontou (D)", value=cur_d, key="mat_ed_d")
                        with cR:
                            etapa_r = st.checkbox(
                                "✅ Revisou (R)", value=cur_r, key="mat_ed_r")
                        with cM:
                            etapa_m = st.checkbox(
                                "✅ Montou (M)", value=cur_m, key="mat_ed_m")
                        with cSem:
                            _semana_ed_default = int(
                                task_ed.get("semana") or _semana_sugerida)
                            nsem = st.number_input("📅 Semana", min_value=0,
                                                   value=_semana_ed_default, step=1, key="mat_sem",
                                                   help=f"Semana sugerida automaticamente: {_semana_sugerida}. "
                                                   "Altere se precisar registrar em outra semana.")
    
                        st.caption(
                            "Marcar D+R+M atualiza o status para Concluído automaticamente.")
    
                        SO = [
                            ("pendente",
                             "⏳ Pendente"),
                            ("em_andamento",
                             "🔄 Em andamento"),
                            ("concluido",
                             "✅ Concluído"),
                            ("travado",
                             "🚫 Travado"),
                            ("nao_aplica",
                             "➖ Não aplica")]
                        kl = [k for k, _ in SO]
                        ll = [v for _, v in SO]
                        ist = kl.index(task_ed["status"]) if task_ed.get(
                            "status") in kl else 0
                        st_col1, st_col2 = st.columns([1, 2])
                        with st_col1:
                            nlbl = st.selectbox(
                                "📌 Status", ll, index=ist, key="mat_st_sel")
                            nst = kl[ll.index(nlbl)]
                        with st_col2:
                            nobs = st.text_area(
                                "💬 Observação",
                                value=task_ed.get("observacao") or "",
                                key="mat_obs_ed",
                                height=80,
                                placeholder="Descreva impedimentos, peças aguardadas, ocorrências...")
    
                        sv_a, sv_b, _ = st.columns([1, 1, 2])
                        with sv_a:
                            save_quick = form_submit_button(
                                "💾 Salvar",
                                key="mat_save_ed",
                                help="Salva as etapas, semana, status e observação da tarefa selecionada.",
                            )
                            if save_quick:
                                new_status = nst
                                if etapa_d and etapa_r and etapa_m:
                                    new_status = "concluido"
    
                                quick_errors = []
                                if new_status == "travado" and not (nobs or "").strip():
                                    quick_errors.append("Preencha a observação antes de salvar uma tarefa como Travado.")
    
                                if quick_errors:
                                    validation_summary(quick_errors, title="Corrija o formulário da tarefa")
                                else:
                                    try:
                                        sb.table("tarefas_servico").update({
                                            "etapa_d": bool(etapa_d), "etapa_r": bool(etapa_r), "etapa_m": bool(etapa_m),
                                            "status": new_status, "semana": int(nsem) if int(nsem) > 0 else None,
                                            "observacao": nobs.strip() or None, "updated_by": current_user_id() or None
                                        }).eq("id", task_ed["id"]).execute()
                                        st.success(
                                            f"✅ Frota {esl} · {svc_name} atualizado!")
                                        bump_data_version()
                                        try:
                                            _load_payload.clear()
                                        except Exception:
                                            pass
                                        try:
                                            _group_kpis.clear()
                                        except Exception:
                                            pass
                                        st.session_state.pop("_mtz_payload_cache", None)
                                        try:
                                            nav.rerun_keep_menu()
                                        except Exception:
                                            st.rerun()
                                    except Exception as e:
                                        st.error(f"Erro: {e}")
                        with sv_b:
                            # Limpar observação rapidamente
                            if (task_ed.get("observacao") or "").strip():
                                if st.button(
                                    "🗑️ Limpar obs.",
                                    use_container_width=True,
                                    key="mat_clear_obs",
                                ):
                                    st.session_state["confirm_clear_obs_matriz"] = True
                                    st.rerun()
    
                                if confirmation_panel(
                                    state_key="confirm_clear_obs_matriz",
                                    title="Confirma limpar a observação desta tarefa?",
                                    body="A observação atual será removida imediatamente da tarefa selecionada.",
                                    confirm_label="Limpar observação",
                                ):
                                    try:
                                        sb.table("tarefas_servico").update(
                                            {"observacao": None}).eq("id", task_ed["id"]).execute()
                                        st.toast("Observação removida.")
                                        bump_data_version()
                                        try:
                                            nav.rerun_keep_menu()
                                        except Exception:
                                            st.rerun()
                                    except Exception as e:
                                        st.error(f"Erro: {e}")
    
                        # ── Histórico de comentários ─────────────────────────────────
                        st.markdown("---")
                        try:
                            from src.ui.components.comentarios import render_comentarios
                            _u_id = current_user_id() or ""
                            _u_nome = st.session_state.get("sb_user_nome") or "Usuário"
                            render_comentarios(
                                tenant_id, task_ed["id"],
                                user_nome=_u_nome,
                                key_prefix=f"mtz_{equip_sel}_{svc_sel}_",
                            )
                        except Exception:
                            pass  # comentários são opcionais — tabela pode não existir ainda
    
        # ── TAB: EXPORTAR ──
        with tab_exportar:
            st.markdown("### Exportações")
            res_exp = resumo_df if (
                isinstance(
                    resumo_df,
                    pd.DataFrame) and not resumo_df.empty) else pd.DataFrame()
            va_exp = view_agg if (
                isinstance(
                    view_agg,
                    pd.DataFrame) and not view_agg.empty) else pd.DataFrame()

            # FIX #13: mostrar contexto (nº linhas) antes dos botões
            _n_res = len(res_exp) if not res_exp.empty else 0
            _n_va = len(va_exp) if not va_exp.empty else 0
            _n_set = len(sector_tables_for_export)

            c1e, c2e = st.columns(2)
            with c1e:
                st.caption(f"📋 Resumo por equipamento — {_n_res} linha(s)")
                _res_sorted = res_exp.sort_values(
                    by=[c for c in ["Score", "%", "Equipamento"] if c in res_exp.columns],
                    ascending=[False, True, True][:sum(1 for c in ["Score", "%", "Equipamento"] if c in res_exp.columns)]
                ) if not res_exp.empty else res_exp
                st.download_button(
                    "⬇️ Baixar resumo (CSV)",
                    data=_df_to_csv_bytes(_res_sorted) if not res_exp.empty else b"",
                    file_name=f"resumo_{grupo_nome}.csv".replace(
                        "/",
                        "-"),
                    mime="text/csv",
                    use_container_width=True,
                    disabled=res_exp.empty)
            with c2e:
                _va_label = "por tarefa" if (
                    "Serviço" in va_exp.columns and not va_exp.empty) else ""
                st.caption(
                    f"⏱️ Tempos de execução {_va_label} — {_n_va} linha(s)")
                st.download_button(
                    "⬇️ Baixar tempos (CSV)",
                    data=_df_to_csv_bytes(va_exp) if not va_exp.empty else b"",
                    file_name=f"tempos_{grupo_nome}.csv".replace(
                        "/",
                        "-"),
                    mime="text/csv",
                    use_container_width=True,
                    disabled=va_exp.empty)

            st.divider()
            st.markdown("#### PDF completo")
            # FIX #3: sector_tables já pré-populado — PDF sempre disponível ao
            # abrir a aba
            if _n_set == 0:
                st.warning(
                    "Nenhum dado de setor disponível para gerar o PDF. Verifique se há equipamentos e template configurados.")
            elif not _reportlab_available():
                st.info(
                    "Instale `reportlab` no requirements.txt para habilitar a exportação em PDF.")
            else:
                st.caption(
                    f"Relatório com {_n_set} setor(es) · {_n_res} equipamento(s)")

                # Evita reaproveitar bytes do grupo/revisão anterior no
                # download.
                export_signature = (
                    str(tenant_id),
                    str(grupo_id),
                    str(revisao_id),
                    str(st.session_state.get("data_version", "0")),
                    int(_n_res),
                    int(_n_set),
                )
                prev_signature = st.session_state.get(
                    "mtz_pdf_export_signature")
                if prev_signature != export_signature:
                    st.session_state.pop("mtz_pdf_export_bytes", None)
                    st.session_state["mtz_pdf_export_signature"] = export_signature

                if "mtz_pdf_export_bytes" not in st.session_state:
                    resumo_pdf_df = resumo_df.copy() if isinstance(
                        resumo_df, pd.DataFrame) else pd.DataFrame()
                    sector_tables_pdf = [
                        (setor_nome, setor_df.copy())
                        for setor_nome, setor_df in (sector_tables_for_export or [])
                    ]
                    st.session_state["mtz_pdf_export_bytes"] = _build_pdf_tables(
                        titulo=titulo,
                        grupo_nome=grupo_nome,
                        resumo_df=resumo_pdf_df,
                        sector_tables=sector_tables_pdf,
                    )

                pdf_bytes = st.session_state["mtz_pdf_export_bytes"]
                pdf_file_name = f"relatorio_matriz_{grupo_nome}.pdf".replace(
                    "/", "-")
                st.download_button(
                    "⬇️ Baixar PDF completo",
                    data=pdf_bytes,
                    file_name=pdf_file_name,
                    mime="application/pdf",
                    use_container_width=True,
                    type="primary",
                    key=f"mtz_pdf_download_{grupo_id}_{revisao_id}_{_n_res}_{_n_set}",
                )

    except Exception as e:
        st.error("Erro ao renderizar a Matriz.")
        st.exception(e)
