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
    nav_to: str | None = None,
    secondary_label: str | None = None,
    secondary_key: str | None = None,
    secondary_nav_to: str | None = None,
) -> bool:
    """Renderiza um estado vazio padronizado.

    Retorna True se o botão de ação principal foi clicado.

    nav_to: se fornecido, clicar no botão principal navega para essa página.
    secondary_label/key/nav_to: botão secundário opcional.
    """
    _inject_once()

    icon_html   = f'<div class="es-icon">{icon}</div>'
    title_html  = f'<div class="es-title">{title}</div>'
    desc_html   = f'<div class="es-desc">{description}</div>' if description else ""
    has_btns    = bool(action_label or secondary_label)
    close_html  = "" if has_btns else "</div>"

    st.markdown(
        f'<div class="es-wrap">{icon_html}{title_html}{desc_html}{close_html}',
        unsafe_allow_html=True,
    )

    clicked = False
    if has_btns:
        btns = [(action_label, action_key, action_type, nav_to)]
        if secondary_label and secondary_key:
            btns.append((secondary_label, secondary_key, "tertiary", secondary_nav_to))

        cols_n = len([b for b in btns if b[0]])
        cols = st.columns([1] + [1.2] * cols_n + [1])
        for i, (lbl, key, btype, page) in enumerate(btns):
            if not lbl or not key:
                continue
            with cols[i + 1]:
                if st.button(lbl, key=key, type=btype, use_container_width=True):
                    clicked = True
                    if page:
                        st.session_state["__nav_to"] = page
                        st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    return clicked
