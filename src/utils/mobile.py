"""Detecção de mobile via JavaScript — sem toggle manual.

Estratégia:
  1. Na primeira visita, um snippet JS mede window.innerWidth e grava
     em session_state via st.query_params (?_mw=<largura>).
  2. A partir do segundo rerun, is_mobile() lê essa largura.
  3. Fallback: query param ?mobile=1 força o modo (atalho para
     criar link direto para operadores de campo).

Threshold: <= 768px = mobile (padrão da indústria para celular/tablet pequeno).
"""
from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

_MOBILE_THRESHOLD = 768
_SESSION_KEY = "__screen_width__"


def _inject_width_detector() -> None:
    """Injeta snippet JS que mede a largura e reescreve o query param _mw.

    Executado uma vez por sessão. Usa st.components para JS puro.
    O rerun é disparado automaticamente via window.location após a medição.
    """
    if st.session_state.get("__width_detected__"):
        return

    # Lê largura atual da URL se já foi gravada antes
    try:
        mw = st.query_params.get("_mw")
        if mw and str(mw).isdigit():
            st.session_state[_SESSION_KEY] = int(mw)
            st.session_state["__width_detected__"] = True
            return
    except Exception:
        pass

    # Injeta JS que mede e redireciona com ?_mw=<largura>
    # height=0 para não ocupar espaço na página
    components.html(
        """
        <script>
        (function() {
            var w = window.innerWidth || document.documentElement.clientWidth || 768;
            var url = new URL(window.location.href);
            if (url.searchParams.get('_mw') !== String(w)) {
                url.searchParams.set('_mw', w);
                window.location.replace(url.toString());
            }
        })();
        </script>
        """,
        height=0,
    )


def is_mobile() -> bool:
    """Retorna True se a tela for menor ou igual a 768px.

    Detecta automaticamente via JS na primeira visita.
    Fallback: ?mobile=1 na URL força o modo mobile.
    """
    # Override manual via URL (útil para atalhos no campo)
    try:
        qp = st.query_params
        if str(qp.get("mobile", "")).lower() in ("1", "true", "yes", "on"):
            return True
    except Exception:
        pass

    width = st.session_state.get(_SESSION_KEY)
    if width is not None:
        return int(width) <= _MOBILE_THRESHOLD

    # Ainda não mediu — chuta False (será corrigido após primeiro rerun)
    return False


def detect_screen_width() -> None:
    """Chama o detector JS. Deve ser chamado no início do app (antes do login).

    Substitui render_mobile_toggle() — sem UI exposta ao usuário.
    """
    _inject_width_detector()
