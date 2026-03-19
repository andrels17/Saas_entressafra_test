from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from src.ui.components.forms import form_submit_button
from src.ui.core.cache import bump_data_version
from src.ui.pages.matriz_runtime import (
    bulk_update_tasks as _bulk_update_tasks,
    sector_is_open as _sector_is_open,
    sector_set_open as _sector_set_open,
    svc_name_map as _svc_name_map,
)
from src.ui.pages.matriz_sector import (
    build_change_preview_lines,
    build_sector_frame,
    sector_progress_label,
    sector_summary_metrics,
    summarize_sector_intelligence,
)
from src.utils import nav
from src.utils.supabase_helpers import normalize_id, current_user_id
from src.utils.timezone import now_utc as _now_utc

from .data import _group_kpis, _load_payload
from .insights import _sector_priority_sort_key
from .pdf_export import _style_heatmap


def _pct_bar_html(pct: int, height: int = 6) -> str:
    pct = max(0, min(100, int(pct or 0)))
    tone = '#12B76A' if pct >= 80 else ('#F59E0B' if pct >= 50 else '#EF4444')
    return (
        f'<div style="margin-top:6px;background:rgba(255,255,255,.08);border-radius:999px;height:{height}px">'
        f'<div style="width:{pct}%;background:{tone};height:{height}px;border-radius:999px;transition:width .35s ease"></div>'
        '</div>'
    )




def _resolve_task_row(sb, task_map, revisao_id, equipamento_id, servico_id):
    """Resolve a tarefa mesmo quando tipos do mapa divergem (str/int)."""
    candidates = [
        (equipamento_id, servico_id),
        (str(equipamento_id), str(servico_id)),
        (str(equipamento_id), servico_id),
        (equipamento_id, str(servico_id)),
    ]
    for key in candidates:
        row = task_map.get(key)
        if row:
            return row

    try:
        rows = (
            sb.table('tarefas_servico')
            .select('id,semana,equipamento_id,servico_id')
            .eq('revisao_id', revisao_id)
            .eq('equipamento_id', equipamento_id)
            .eq('servico_id', servico_id)
            .limit(1)
            .execute()
            .data
        ) or []
        if rows:
            row = rows[0]
            task_map[(equipamento_id, servico_id)] = row
            task_map[(str(equipamento_id), str(servico_id))] = row
            return row
    except Exception as _e:
        import logging; logging.getLogger("saas").debug("_get_or_create_task: %s", _e)
    return {}

def _render_sector_editor(*, sb, revisao_id, grupo_id, setor_nome, svs, svc_ids_v, svc_names_v, eqs, task_map, eq_label_short, rev_start, atraso_dias, semana_lote):
    df, col_meta, obs_map = build_sector_frame(
        equipamentos=eqs,
        svc_ids=svc_ids_v,
        svc_names=svc_names_v,
        task_map=task_map,
        eq_label_short=eq_label_short,
    )
    if df.empty:
        st.info('Sem dados para este setor.')
        return

    df_display = df.set_index('_equip_id', drop=True)
    svc_bool = [c for c in df_display.columns if c not in ('%', 'Equipamento')]
    tok_s, tc_s, pg, pm, eq_100s = sector_summary_metrics(df_display, svc_bool)
    c1s, c2s, c3s = st.columns([1, 1, 2])
    c1s.metric('Geral (ponderado)', f'{pg}%')
    c2s.metric('Médio (frotas)', f'{pm}%')
    with c3s:
        eq100_html = f' &nbsp;·&nbsp; <b style="color:#12B76A">{eq_100s}</b> 100%' if eq_100s > 0 else ''
        st.markdown(
            f'<div style="padding-top:8px;font-size:.82rem;color:rgba(255,255,255,.65)">'
            f'{len(df)} eq &nbsp;·&nbsp; {len(svc_ids_v)} serviços &nbsp;·&nbsp; '
            f'<b style="color:rgba(255,255,255,.9)">{tok_s}/{tc_s}</b> concluídas'
            f'{eq100_html}'
            f'</div>'
            f'{_pct_bar_html(pg, height=4)}',
            unsafe_allow_html=True,
        )

    df_display = df_display.drop(columns=['%'], errors='ignore')
    if 'Status' not in df_display.columns:
        df_display.insert(
            0,
            'Status',
            df_display.apply(
                lambda rw: '✓' if all(bool(rw.get(c, False)) for c in svc_bool) else '',
                axis=1,
            ) if svc_bool else None,
        )

    if obs_map:
        with st.expander(f'💬 Observações ({len(obs_map)})', expanded=False):
            svc_names_map = _svc_name_map(svs)
            for key, obs_txt in obs_map.items():
                eid_k, sid_k = key.split('__')
                eq_n = eq_label_short.get(eid_k, eid_k)
                svc_n = svc_names_map.get(str(sid_k), sid_k)
                st.markdown(f'**Frota {eq_n}** · {svc_n}: _{obs_txt}_')

    svc_names_map = _svc_name_map(svs)
    kb = f'mat_ed_{revisao_id}_{grupo_id}_{setor_nome}'.replace(' ', '_')
    mode = st.radio('Visualização', ['Editar', 'Visual'], horizontal=True, key=f'mtz_mode_{kb}')

    if mode == 'Visual':
        days_since = int((pd.Timestamp(_now_utc()) - rev_start).days) if isinstance(rev_start, pd.Timestamp) else 0
        df_vis = df_display.copy()
        for c in svc_bool:
            df_vis[c] = df_vis[c].apply(lambda v: 'OK' if bool(v) else '')
        if days_since > atraso_dias:
            for c in [c for c in svc_bool if str(c).strip().endswith(' M')]:
                df_vis.loc[df_vis[c] == '', c] = '!'
        st.dataframe(df_vis.style.apply(_style_heatmap, axis=None), use_container_width=True, hide_index=True)
        edited = None
    else:
        edited = st.data_editor(
            df_display,
            key=kb,
            use_container_width=True,
            hide_index=True,
            column_config={
                'Status': st.column_config.TextColumn('✓', disabled=True, width='small'),
                'Equipamento': st.column_config.TextColumn('Equipamento', disabled=True),
                **{col: st.column_config.CheckboxColumn(col) for col in svc_bool},
            },
            disabled=['Status', 'Equipamento'],
        )

    sv1, sv2, _ = st.columns([1.2, 1.8, 1])
    with sv1:
        save_now = form_submit_button(
            '💾 Salvar alterações',
            key=f'save_{kb}',
            help='Valida e prepara as alterações feitas no grid deste setor antes da confirmação final.',
        )
    with sv2:
        st.caption('Marque/desmarque etapas acima e clique em Salvar.')

    pending_changes_key = f'pending_changes_{kb}'
    pending_preview_key = f'pending_preview_{kb}'
    field_lbl = {'etapa_d': 'D', 'etapa_r': 'R', 'etapa_m': 'M'}

    if save_now:
        if edited is None:
            st.warning('Troque para o modo **Editar** para poder salvar alterações.')
        else:
            changes = []
            for equip_id, row in edited.iterrows():
                if equip_id not in df_display.index:
                    continue
                for col in svc_bool:
                    ov = bool(df_display.loc[equip_id, col])
                    nv = bool(row[col])
                    if ov != nv:
                        sid, field = col_meta[col]
                        changes.append((str(equip_id), str(sid), field, nv))
            if not changes:
                st.session_state.pop(pending_changes_key, None)
                st.session_state.pop(pending_preview_key, None)
                st.info('Nenhuma alteração detectada — faça alguma marcação antes de salvar.')
            else:
                prev_lines = build_change_preview_lines(
                    changes,
                    eq_label_short=eq_label_short,
                    svc_names=svc_names_map,
                    field_labels=field_lbl,
                    limit=8,
                )
                st.session_state[pending_changes_key] = changes
                st.session_state[pending_preview_key] = prev_lines
                st.rerun()

    pending_changes = st.session_state.get(pending_changes_key) or []
    pending_preview = st.session_state.get(pending_preview_key) or []
    if pending_changes:
        with st.container(border=True):
            st.markdown(f'**{len(pending_changes)} alteração(ões) a salvar:**')
            st.markdown('\n'.join(pending_preview))
            c_yes, c_no, _ = st.columns([1, 1, 2])
            with c_yes:
                confirm_now = st.button('✅ Confirmar', key=f'yes_{kb}', type='primary', use_container_width=True)
            with c_no:
                cancel_now = st.button('✖ Cancelar', key=f'no_{kb}', use_container_width=True)

        if cancel_now:
            st.session_state.pop(pending_changes_key, None)
            st.session_state.pop(pending_preview_key, None)
            st.rerun()

        if confirm_now:
            now_iso = datetime.now(timezone.utc).isoformat()
            missing = 0
            payload_updates = []
            for eid, sid, field, nv in pending_changes:
                t = _resolve_task_row(sb, task_map, revisao_id, eid, sid)
                tid = t.get('id')
                if not tid:
                    missing += 1
                    continue
                upd = {'id': tid, field: bool(nv), 'updated_by': current_user_id() or None}
                dtf = {'etapa_d': 'dt_etapa_d', 'etapa_r': 'dt_etapa_r', 'etapa_m': 'dt_etapa_m'}.get(field)
                if dtf:
                    upd[dtf] = now_iso if nv else None
                if nv and not t.get('semana') and int(semana_lote) > 0:
                    upd['semana'] = int(semana_lote)
                payload_updates.append(upd)

            pb = st.empty()
            st.session_state.pop(pending_changes_key, None)
            st.session_state.pop(pending_preview_key, None)
            if not payload_updates:
                pb.error('Nenhuma tarefa elegível foi encontrada para salvar as alterações selecionadas.')
                if missing:
                    st.caption(f'Itens sem correspondência de tarefa: {missing}.')
            else:
                with st.spinner(f'Aplicando {len(payload_updates)} alterações em lote...'):
                    ok, failed = _bulk_update_tasks(sb, payload_updates)

                if ok <= 0:
                    pb.error('Não foi possível persistir as alterações desta seleção.')
                else:
                    pb.success(
                        f'✅ {ok} etapas salvas'
                        + (f'  ·  {failed} falharam' if failed else '')
                        + (f'  ·  {missing} não encontradas' if missing else '')
                    )
                    st.toast('✅ Alterações aplicadas com sucesso!')
                    bump_data_version()
                    try:
                        _load_payload.clear()
                    except Exception:
                        pass  # cache.clear() pode falhar sem bloquear o fluxo
                    try:
                        _group_kpis.clear()
                    except Exception:
                        pass  # cache.clear() pode falhar sem bloquear o fluxo
                    try:
                        nav.rerun_keep_menu()
                    except Exception:
                        st.rerun()



def render_matrix_tab(*, sb, revisao_id, grupo_id, group_atraso_dias, semanas_disp, semana_sugerida, group_rev_start, setor_to_services, tarefas, eqs, task_map, eq_label_short):
    st.markdown('### Drill-down por setor')
    st.caption('Marque as etapas (D/R/M) direto na tabela. Setores 🔴 são prioridade — expanda para editar.')

    atraso_dias = group_atraso_dias
    fc1, fc2, fc3 = st.columns([1, 1.5, 1.5])
    with fc1:
        atraso_dias = st.number_input(
            'Atraso (dias)',
            min_value=1,
            max_value=90,
            value=atraso_dias,
            step=1,
            key='mtz_atraso_in',
            help='Marca coluna M como atraso quando passou mais de X dias.',
        )
        st.session_state['matriz_atraso_dias'] = int(atraso_dias)
    with fc2:
        sem_opts = ['Todas as semanas'] + [f'Semana {s}' for s in semanas_disp]
        sem_pick = st.selectbox('Filtrar por semana', sem_opts, index=0, key='mtz_sem_pick')
        semana_filtro = None if sem_pick == 'Todas as semanas' else int(sem_pick.split()[-1])
    with fc3:
        semana_lote = st.number_input(
            '📅 Semana do apontamento',
            min_value=0,
            max_value=99,
            value=int(semana_sugerida),
            step=1,
            key='mtz_semana_lote',
            help=(
                f'Semana sugerida automaticamente ({semana_sugerida}) com base na data de início da revisão. '
                'Altere se estiver registrando uma etapa de outra semana. '
                'Aplicada apenas em tarefas que ainda não têm semana definida.'
            ),
        )

    rev_start = group_rev_start
    chip_target = st.session_state.pop('mtz_chip_jump', None)

    sector_intelligence = []
    for setor_nome in sorted(setor_to_services.keys(), key=lambda x: x.lower()):
        svs_all = sorted(setor_to_services[setor_nome], key=lambda x: (x.get('nome') or '').lower())
        svc_ids_all = [s['id'] for s in svs_all if s.get('id')]
        if semana_filtro is not None:
            svc_na_sem = {t['servico_id'] for t in tarefas if t.get('semana') == semana_filtro and t.get('servico_id')}
            svc_ids_all = [sid for sid in svc_ids_all if sid in svc_na_sem]
        if not svc_ids_all:
            continue
        intel = summarize_sector_intelligence(
            equipamentos=eqs,
            svc_ids=svc_ids_all,
            task_map=task_map,
            atraso_dias=int(atraso_dias),
            rev_start=rev_start,
        )
        intel['setor_nome'] = setor_nome
        sector_intelligence.append(intel)

    if sector_intelligence:
        priority_sorted = sorted(sector_intelligence, key=_sector_priority_sort_key)
        st.markdown('<div class="mtz-priority-panel">', unsafe_allow_html=True)
        st.markdown('#### 🔥 Prioridades agora')
        for idx, item in enumerate(priority_sorted[:3], start=1):
            st.markdown(
                f'<div class="mtz-priority-item"><b>{idx}. {item["setor_nome"]}</b> · '
                f'{item["risk_icon"]} risco {item["risk_label"]} · '
                f'<b>{item["pct"]}%</b> concluído · '
                f'{item["criticos"]} críticos · '
                f'{item["atrasadas_m"]} atraso(s) de montagem<br>'
                f'<span style="opacity:.78">{item["recommendation"]}</span></div>',
                unsafe_allow_html=True,
            )
        st.markdown('</div>', unsafe_allow_html=True)

    for setor_nome in sorted(setor_to_services.keys(), key=lambda x: x.lower()):
        svs = sorted(setor_to_services[setor_nome], key=lambda x: (x.get('nome') or '').lower())
        svc_ids = [s['id'] for s in svs if s.get('id')]
        svc_names = [s.get('nome') or str(s.get('id')) for s in svs if s.get('id')]
        if not svc_ids:
            continue
        if semana_filtro is not None:
            svc_na_sem = {t['servico_id'] for t in tarefas if t.get('semana') == semana_filtro and t.get('servico_id')}
            svc_ids_v = [sid for sid in svc_ids if sid in svc_na_sem]
            svc_names_v = [svc_names[i] for i, sid in enumerate(svc_ids) if sid in svc_na_sem]
            if not svc_ids_v:
                continue
        else:
            svc_ids_v = svc_ids
            svc_names_v = svc_names

        done_s, tot_s, pct_s, lbl_exp = sector_progress_label(
            equipamentos=eqs,
            svc_ids=svc_ids_v,
            task_map=task_map,
            setor_nome=setor_nome,
        )
        _ = (done_s, tot_s)

        auto_expand = (pct_s == 0) or (setor_nome == chip_target)
        if auto_expand and not _sector_is_open(revisao_id, grupo_id, setor_nome):
            _sector_set_open(revisao_id, grupo_id, setor_nome, True)

        sector_open = _sector_is_open(revisao_id, grupo_id, setor_nome)
        sector_intel = summarize_sector_intelligence(
            equipamentos=eqs,
            svc_ids=svc_ids_v,
            task_map=task_map,
            atraso_dias=int(atraso_dias),
            rev_start=rev_start,
        )
        risk_class = 'high' if sector_intel['risk'] == 'alto' else ('medium' if sector_intel['risk'] == 'medio' else 'low')

        st.markdown(f'<div class="mtz-sector-box {risk_class}">', unsafe_allow_html=True)
        with st.container():
            head_l, head_r = st.columns([0.78, 0.22])
            with head_l:
                st.markdown(f'#### {lbl_exp}')
                st.markdown(
                    '<div class="mtz-risk-badges">'
                    f'<span class="mtz-risk-badge {risk_class}">{sector_intel["risk_icon"]} Risco {sector_intel["risk_label"]}</span>'
                    f'<span class="mtz-risk-badge {"high" if sector_intel["criticos"] else "low"}">Críticos: {sector_intel["criticos"]}</span>'
                    f'<span class="mtz-risk-badge {"medium" if sector_intel["em_andamento"] else "low"}">Em andamento: {sector_intel["em_andamento"]}</span>'
                    f'<span class="mtz-risk-badge {"high" if sector_intel["atrasadas_m"] else "low"}">Atraso M: {sector_intel["atrasadas_m"]}</span>'
                    f'<span class="mtz-risk-badge {"medium" if sector_intel["sem_inicio"] else "low"}">Sem início: {sector_intel["sem_inicio"]}</span>'
                    '</div>',
                    unsafe_allow_html=True,
                )
                st.caption(sector_intel['recommendation'])
            with head_r:
                toggle_label = 'Ocultar setor' if sector_open else 'Abrir setor'
                if st.button(
                    toggle_label,
                    key=f'mtz_toggle_sector_{revisao_id}_{grupo_id}_{setor_nome}'.replace(' ', '_'),
                    use_container_width=True,
                ):
                    _sector_set_open(revisao_id, grupo_id, setor_nome, not sector_open)
                    st.rerun()

            if not sector_open:
                st.caption('Clique em **Abrir setor** para carregar a grade e editar apenas este setor.')
                st.markdown('</div>', unsafe_allow_html=True)
                continue

            _render_sector_editor(
                sb=sb,
                revisao_id=revisao_id,
                grupo_id=grupo_id,
                setor_nome=setor_nome,
                svs=svs,
                svc_ids_v=svc_ids_v,
                svc_names_v=svc_names_v,
                eqs=eqs,
                task_map=task_map,
                eq_label_short=eq_label_short,
                rev_start=rev_start,
                atraso_dias=atraso_dias,
                semana_lote=semana_lote,
            )
        st.markdown('</div>', unsafe_allow_html=True)
