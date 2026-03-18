from __future__ import annotations

import streamlit as st

from src.ui.pages.matriz_runtime import _sector_set_open

from .styles import _pct_bar_html


def render_group_header(*, placeholder, grupo_nome, titulo, eqs, pct_geral, eq100_g, setor_rows, revisao_id, grupo_id):
    with placeholder.container():
        st.markdown('<div class="enterprise-sticky">', unsafe_allow_html=True)
        c_l, c_r = st.columns([6, 1], vertical_alignment="center")
        with c_l:
            st.markdown(f'<div class="enterprise-title">{grupo_nome}</div>', unsafe_allow_html=True)
            st.markdown(
                f'<div class="enterprise-sub">Revisão: <b>{titulo}</b>  ·  Equip.: <b>{len(eqs)}</b>  ·  Geral: <b>{pct_geral}%</b>  ·  100%: <b>{eq100_g}/{len(eqs)}</b></div>',
                unsafe_allow_html=True,
            )
            st.markdown(_pct_bar_html(pct_geral, height=8), unsafe_allow_html=True)
        with c_r:
            if st.button("← Voltar", key="mtz_back_hdr", use_container_width=True):
                st.session_state["matriz_view"] = "select"
                st.rerun()

        if setor_rows:
            st.markdown('<div class="enterprise-divider"></div>', unsafe_allow_html=True)
            st.markdown(
                '<div class="enterprise-chip-row" style="flex-wrap:wrap;gap:6px;display:flex;margin-top:6px">',
                unsafe_allow_html=True,
            )
            chip_cols = st.columns(min(len(setor_rows[:12]), 6))
            for ci, r in enumerate(setor_rows[:12]):
                ratio = r["ok_eq"] / max(r["total_eq"], 1)
                icon = "🟢" if ratio >= 0.8 else ("🟡" if ratio >= 0.5 else "🔴")
                lbl = f"{icon} {r['setor']} {r['ok_eq']}/{r['total_eq']}"
                with chip_cols[ci % len(chip_cols)]:
                    if st.button(
                        lbl,
                        key=f"chip_setor_{ci}_{r['setor']}".replace(" ", "_"),
                        use_container_width=True,
                        help=f"{r['setor']}: {r['pct_med']}% médio · {r['ok_eq']}/{r['total_eq']} equip. 100%",
                    ):
                        st.session_state["mtz_chip_jump"] = r["setor"]
                        _sector_set_open(revisao_id, grupo_id, r["setor"], True)
                        st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)
