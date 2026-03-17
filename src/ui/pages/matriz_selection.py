
from __future__ import annotations

import streamlit as st


def render_selection_context(
    *,
    is_group_view: bool,
    grupos: list[dict],
    grupo_id,
    departamento_id,
    is_admin: bool,
    dept_name_fn,
) -> tuple[bool, bool]:
    """Renderiza chips/contexto da seleção e retorna ações do usuário."""
    col_chip, col_actions = st.columns([1.6, 1.2])

    with col_chip:
        if is_group_view:
            gn = next((g.get("nome") for g in grupos if g.get("id") == grupo_id), "—")
            st.markdown(
                f'<div class="enterprise-chip"><strong>Grupo:</strong> {gn}</div>',
                unsafe_allow_html=True,
            )
        elif departamento_id and is_admin:
            dn = dept_name_fn(departamento_id) or "(departamento)"
            st.markdown(
                f'<div class="enterprise-chip"><strong>Depto:</strong> {dn}</div>',
                unsafe_allow_html=True,
            )

    with col_actions:
        if not is_group_view and is_adminmin:
            c1, c2 = st.columns(2)
            with c1:
                clear_dept = st.button("Limpar depto", key="mtz_clear_dept", use_container_width=True)
            with c2:
                show_all = st.button("Ver todos", key="mtz_show_all", use_container_width=True)
            return clear_dept, show_all

    return False, False
