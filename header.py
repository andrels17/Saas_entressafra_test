from __future__ import annotations
from html import escape as _h

import streamlit as st

from .styles import _pct_bar_html


def render_group_header(*, placeholder, grupo_nome, titulo, eqs, pct_geral, eq100_g, setor_rows, revisao_id, grupo_id) -> None:
    with placeholder.container():
        st.markdown('<div class="enterprise-sticky">', unsafe_allow_html=True)
        c_l, c_r = st.columns([6, 1], vertical_alignment="center")
        with c_l:
            st.markdown(f'<div class="enterprise-title">{_h(str(grupo_nome))}</div>', unsafe_allow_html=True)
            st.markdown(
                f'<div class="enterprise-sub">Revisão: <b>{_h(str(titulo))}</b>  ·  Equip.: <b>{len(eqs)}</b>  ·  Geral: <b>{pct_geral}%</b>  ·  100%: <b>{eq100_g}/{len(eqs)}</b></div>',
                unsafe_allow_html=True,
            )
            st.markdown(_pct_bar_html(pct_geral, height=8), unsafe_allow_html=True)
        with c_r:
            if st.button("← Voltar", key="mtz_back_hdr", use_container_width=True):
                st.session_state["matriz_view"] = "select"
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
