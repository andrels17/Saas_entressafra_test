"""Diálogo de confirmação para ações destrutivas ou bulk.

Usa st.dialog (nativo 1.42+) para não bloquear o restante da página.

Uso:
    from src.ui.core.confirm_dialog import confirm_dialog

    if st.button("Concluir tudo"):
        st.session_state["_confirm_batch_done"] = True

    confirmed = confirm_dialog(
        trigger_key="_confirm_batch_done",
        title="Concluir todas as tarefas da semana?",
        body="Esta ação atualiza {n} tarefas para 'Concluído'. Não pode ser desfeita.",
        confirm_label="Confirmar",
        cancel_label="Cancelar",
    )
    if confirmed:
        ...  # executa ação
"""
from __future__ import annotations

import streamlit as st


def confirm_dialog(
    trigger_key: str,
    title: str,
    body: str = "",
    confirm_label: str = "Confirmar",
    cancel_label: str = "Cancelar",
    danger: bool = False,
) -> bool:
    """Exibe um st.dialog de confirmação quando trigger_key estiver True no session_state.

    Retorna True apenas no frame em que o usuário confirmou.
    """
    if not st.session_state.get(trigger_key):
        return False

    confirmed = False

    @st.dialog(title)
    def _dialog():
        nonlocal confirmed
        if body:
            st.markdown(body)
            st.markdown("")
        col_cancel, col_confirm = st.columns(2)
        with col_cancel:
            if st.button(
                    cancel_label,
                    use_container_width=True,
                    key=f"{trigger_key}_cancel"):
                st.session_state.pop(trigger_key, None)
                st.rerun()
        with col_confirm:
            if st.button(
                confirm_label,
                use_container_width=True,
                type="primary",
                key=f"{trigger_key}_confirm",
            ):
                st.session_state.pop(trigger_key, None)
                confirmed = True
                st.rerun()

    _dialog()
    return confirmed
