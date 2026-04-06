"""Utilitário de escape HTML para renderização segura com unsafe_allow_html=True.

Uso:
    from src.ui.core.html_safe import h

    st.markdown(f'<div>{h(nome_do_banco)}</div>', unsafe_allow_html=True)

`h()` é um alias curto para html.escape() que trata None graciosamente.
"""
from __future__ import annotations
import html as _html


def h(value: object, fallback: str = "—") -> str:
    """Escapa HTML de um valor arbitrário vindo do banco ou do usuário.

    - None / vazio → retorna `fallback`
    - Qualquer string → escapa <, >, &, ", '
    """
    if value is None:
        return fallback
    s = str(value).strip()
    if not s:
        return fallback
    return _html.escape(s)


def hn(value: object) -> str:
    """Versão sem fallback — retorna string vazia se None/vazio."""
    if value is None:
        return ""
    return _html.escape(str(value).strip())
