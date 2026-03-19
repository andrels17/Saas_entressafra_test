from __future__ import annotations

import time
from typing import Any

import streamlit as st


def bump_data_version() -> str:
    """Gera um novo token de versão para invalidar caches que dependem de data_version."""
    token = f"{time.time():.6f}"
    st.session_state["data_version"] = token
    return token


def clear_cached_functions(*funcs: Any) -> None:
    """Tenta limpar apenas caches pontuais, sem derrubar todos os caches da sessão."""
    for fn in funcs:
        clear = getattr(fn, "clear", None)
        if callable(clear):
            try:
                clear()
            except Exception:
                pass  # ignorado — operação opcional
