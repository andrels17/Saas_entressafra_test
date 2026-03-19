from __future__ import annotations

import streamlit as st


def refresh_button(key: str, label: str = "Atualizar", *, help: str | None = None) -> bool:
    """Botão padrão de atualização de tela."""
    return st.button(
        label,
        icon=":material/refresh:",
        use_container_width=True,
        help=help,
        key=key,
    )


def primary_action_button(
    label: str,
    *,
    key: str,
    icon: str = ":material/save:",
    help: str | None = None,
) -> bool:
    """Botão primário padrão para ações de salvar/confirmar."""
    return st.button(
        label,
        icon=icon,
        type="primary",
        use_container_width=True,
        help=help,
        key=key,
    )


def download_action(
    label: str,
    *,
    data,
    file_name: str,
    mime: str,
    key: str,
    icon: str = ":material/download:",
    help: str | None = None,
    type: str | None = None,
) -> None:
    """Wrapper padronizado para downloads."""
    kwargs = dict(
        label=label,
        data=data,
        file_name=file_name,
        mime=mime,
        icon=icon,
        use_container_width=True,
        help=help,
        key=key,
    )
    if type:
        kwargs["type"] = type
    st.download_button(**kwargs)
