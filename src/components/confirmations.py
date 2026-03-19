
from __future__ import annotations

import streamlit as st


def confirmation_panel(
    *,
    state_key: str,
    title: str,
    body: str,
    confirm_label: str = "Confirmar",
    cancel_label: str = "Cancelar",
) -> bool:
    """Exibe um painel inline de confirmação enquanto `state_key` estiver ativo."""
    if not st.session_state.get(state_key):
        return False

    confirmed = False
    with st.container(border=True):
        st.warning(title)
        st.caption(body)
        c1, c2 = st.columns([1, 1])
        with c1:
            if st.button(confirm_label, key=f"{state_key}_confirm", type="primary", use_container_width=True):
                st.session_state.pop(state_key, None)
                confirmed = True
        with c2:
            if st.button(cancel_label, key=f"{state_key}_cancel", use_container_width=True):
                st.session_state.pop(state_key, None)
                st.rerun()
    return confirmed
