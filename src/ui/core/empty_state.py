"""Componente padronizado de estado vazio.

Uso:
    from src.ui.core.empty_state import empty_state

    empty_state(
        icon="◫",
        title="Nenhum equipamento cadastrado",
        description="Importe uma planilha ou adicione manualmente para começar.",
        action_label="Importar CSV",
        action_key="eq_import_btn",
    )
    if st.session_state.get("eq_import_btn"):
        ...
"""
from __future__ import annotations

import streamlit as st


_CSS = """
<style>
.es-wrap {
    display: flex; flex-direction: column; align-items: center;
    justify-content: center; padding: 3rem 1rem; text-align: center;
}
.es-icon {
    font-size: 2rem; line-height: 1;
    color: var(--dim, #6B7A73);
    margin-bottom: 0.9rem;
    opacity: 0.7;
}
.es-title {
    font-size: 1rem; font-weight: 700;
    color: var(--muted, #B8C2BD);
    margin-bottom: 0.35rem;
}
.es-desc {
    font-size: 0.82rem; color: var(--dim, #6B7A73);
    max-width: 380px; line-height: 1.55;
    margin-bottom: 1.4rem;
}
</style>
"""

_CSS_INJECTED = False


def _inject_once() -> None:
    global _CSS_INJECTED
    if not _CSS_INJECTED:
        st.markdown(_CSS, unsafe_allow_html=True)
        _CSS_INJECTED = True


def empty_state(
    title: str,
    description: str = "",
    icon: str = "○",
    action_label: str | None = None,
    action_key: str | None = None,
    action_type: str = "secondary",
) -> bool:
    """Renderiza um estado vazio padronizado.

    Retorna True se o botão de ação foi clicado.
    """
    _inject_once()

    icon_html   = f'<div class="es-icon">{icon}</div>'
    title_html  = f'<div class="es-title">{title}</div>'
    desc_html   = f'<div class="es-desc">{description}</div>' if description else ""
    close_html  = "</div>" if not action_label else ""

    st.markdown(
        f'<div class="es-wrap">{icon_html}{title_html}{desc_html}{close_html}',
        unsafe_allow_html=True,
    )

    clicked = False
    if action_label and action_key:
        _, center, _ = st.columns([1, 1.4, 1])
        with center:
            clicked = st.button(
                action_label,
                key=action_key,
                type=action_type,
                use_container_width=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)

    return clicked
