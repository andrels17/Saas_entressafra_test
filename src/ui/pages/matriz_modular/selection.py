from __future__ import annotations

import streamlit as st

from .data import _all_dept_names, _group_kpis
from .styles import (
    _card_status_badge,
    _truncate_card_subtitle,
    _truncate_card_title,
)


def render_selection_screen(*, tenant_id, revisao_id, grupos, search, status_filter, sort_by, data_version):
    """Renderiza a grade de seleção de grupos.

    Retorna True quando a tela de seleção foi renderizada e o fluxo deve encerrar.
    Retorna False quando a página deve continuar para a visão de grupo.
    """
    if st.session_state.get("matriz_view") == "group":
        return False

    kpis = _group_kpis(tenant_id, revisao_id, data_version, st.session_state.get("sb_access_token", "")) if revisao_id else {}
    q = (search or "").strip().lower()
    dep_id = st.session_state.get("matriz_departamento_id")
    dept_names = _all_dept_names(tenant_id, data_version, st.session_state.get("sb_access_token", ""))

    show_groups = [
        g for g in grupos
        if (not dep_id or g.get("departamento_id") == dep_id)
        and (
            (not q)
            or (q in (g.get("nome") or "").lower())
            or (q in (dept_names.get(g.get("departamento_id"), "")).lower())
        )
    ]

    if status_filter != "Todos":
        def _status_match(g):
            p = int(kpis.get(g.get("id"), {}).get("pct", 0))
            eq = int(kpis.get(g.get("id"), {}).get("eq_count", 0))
            if status_filter.startswith("🔴"):
                return p < 50 and eq > 0
            if status_filter.startswith("🟡"):
                return 50 <= p < 80
            if status_filter.startswith("🟢"):
                return p >= 80
            if status_filter.startswith("⬜"):
                return eq == 0
            return True

        show_groups = [g for g in show_groups if _status_match(g)]

    if sort_by.startswith("% ↑"):
        show_groups = sorted(show_groups, key=lambda g: kpis.get(g.get("id"), {}).get("pct", 0))
    elif sort_by.startswith("% ↓"):
        show_groups = sorted(show_groups, key=lambda g: -kpis.get(g.get("id"), {}).get("pct", 0))
    else:
        show_groups = sorted(show_groups, key=lambda g: (g.get("nome") or "").lower())

    if not show_groups:
        st.info("Nenhum grupo encontrado para os filtros selecionados.")

    st.markdown('<div class="mtz-card-grid">', unsafe_allow_html=True)
    for row_start in range(0, len(show_groups), 3):
        row_groups = show_groups[row_start:row_start + 3]
        cols = st.columns(3)
        for col_idx, g in enumerate(row_groups):
            gid = g.get("id")
            nome = g.get("nome") or str(gid)
            info = kpis.get(gid, {})
            pct = int(info.get("pct", 0))
            eqc = int(info.get("eq_count", 0))
            svc = int(info.get("svc_count", 0))
            dept_lbl = dept_names.get(g.get("departamento_id"), "")
            with cols[col_idx]:
                status_txt, status_cls = _card_status_badge(pct, eqc, svc)
                ring_cls = (
                    "high" if status_cls == "critico"
                    else "medium" if status_cls == "atencao"
                    else "low" if status_cls == "avancado"
                    else "neutral"
                )
                with st.container(border=True):
                    st.markdown(f'<div class="mtz-select-card {ring_cls}">', unsafe_allow_html=True)
                    st.markdown(
                        f'<div class="mtz-card-title">{_truncate_card_title(nome, 22)}</div>',
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        f'<div class="mtz-card-subtitle">{_truncate_card_subtitle(dept_lbl, 20)}</div>',
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        f'<div class="mtz-card-metrics">{pct}% · {eqc} eq · {svc} svc</div>',
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        f'<div class="mtz-card-status {status_cls}">{status_txt}</div>',
                        unsafe_allow_html=True,
                    )
                    if st.button(
                        "Abrir",
                        key=f"mtz_card_{gid}",
                        use_container_width=True,
                        help=f"{nome} · {dept_lbl or 'Sem departamento'} · {pct}% concluído · {eqc} equipamentos · {svc} serviços",
                    ):
                        st.session_state["matriz_grupo_id"] = gid
                        st.session_state["matriz_view"] = "group"
                        st.rerun()
                    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)
    return True
