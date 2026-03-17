
from __future__ import annotations

import streamlit as st


def render_selection_header(
    *,
    is_group_view: bool,
    grupos: list[dict],
    grupo_id,
    departamento_id,
    is_admin: bool,
    dept_name_fn,
) -> None:
    if is_group_view:
        gn = next((g.get("nome") for g in grupos if g.get("id") == grupo_id), "—")
        st.markdown(
            f'<div class="enterprise-chip"><strong>Grupo:</strong> {gn}</div>',
            unsafe_allow_html=True,
        )
        return

    if departamento_id and is_admin:
        dn = dept_name_fn(departamento_id) or "(departamento)"
        st.markdown(
            f'<div class="enterprise-chip"><strong>Depto:</strong> {dn}</div>',
            unsafe_allow_html=True,
        )


def render_group_scope_actions(*, is_group_view: bool, is_admin: bool) -> tuple[bool, bool]:
    cleared_dept = False
    show_all = False

    if not is_group_view and is_admin:
        if st.button("Limpar depto", key="mtz_clear_dept", use_container_width=True):
            cleared_dept = True
        if st.button("Ver todos", key="mtz_show_all", use_container_width=True):
            show_all = True

    return cleared_dept, show_all
