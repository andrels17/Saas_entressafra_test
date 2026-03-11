"""Utilitários de navegação para o app Streamlit.

Fonte única de verdade para:
  - revisao_id atual (get/set)
  - Navegação programática entre páginas (goto)
"""
from __future__ import annotations

import streamlit as st

# ── Revisão atual ─────────────────────────────────────────────────────────────
_REVISAO_KEY = "current_revisao_id"
# Aliases legados para retrocompatibilidade de caches existentes (somente leitura)
_REVISAO_ALIASES = ("matriz_revisao_id", "home_revisao_id", "dashboard_revisao_id", "revisao_id")


def get_current_revisao() -> str | None:
    """Retorna o revisao_id canônico. Faz fallback silencioso para aliases legados."""
    v = st.session_state.get(_REVISAO_KEY)
    if v:
        return v
    for alias in _REVISAO_ALIASES:
        v = st.session_state.get(alias)
        if v:
            # Migra silenciosamente para a chave canônica
            st.session_state[_REVISAO_KEY] = v
            return v
    return None


def set_current_revisao(revisao_id: str | None) -> None:
    """Persiste o revisao_id canônico e mantém aliases sincronizados."""
    st.session_state[_REVISAO_KEY] = revisao_id
    for alias in _REVISAO_ALIASES:
        st.session_state[alias] = revisao_id


# ── Navegação ─────────────────────────────────────────────────────────────────

def goto(page_name: str) -> None:
    """Navega para uma página e reexecuta o app.

    IMPORTANTE: não escreve direto na chave do selectbox (__menu) para evitar
    ``StreamlitAPIException``. Usa a chave ``__nav_to`` que app.py lê antes
    de montar a sidebar.
    """
    st.session_state["__nav_to"] = page_name
    st.rerun()


def rerun_keep_menu() -> None:
    """Rerun preservando a página atual (alias de st.rerun para retrocompatibilidade)."""
    st.rerun()

