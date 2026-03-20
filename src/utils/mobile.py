"""Detecção automática de mobile via JavaScript — sem toggle manual.

Estratégia:
  1. Um snippet JS mede window.innerWidth e grava em session_state
     via st.query_params (?_mw=<largura>) na primeira visita.
  2. A partir do segundo rerun, is_mobile() lê essa largura.
  3. Fallback: ?mobile=1 força o modo (atalho para operadores de campo).

Threshold: <= 768px = mobile.
"""
from __future__ import annotations

import streamlit as st

_MOBILE_THRESHOLD = 768
_SESSION_KEY = "__screen_width__"


def _inject_width_detector() -> None:
    """Injeta JS que mede a largura da tela e reescreve ?_mw= na URL.

    Usa st.markdown com unsafe_allow_html — sem componente externo,
    sem renderização de texto indesejada.
    """
    if st.session_state.get("__width_detected__"):
        return

    # Lê largura já medida na URL
    try:
        mw = st.query_params.get("_mw")
        if mw and str(mw).isdigit():
            st.session_state[_SESSION_KEY] = int(mw)
            st.session_state["__width_detected__"] = True
            return
    except Exception:
        pass

    # Injeta script inline — não renderiza texto, apenas executa JS
    st.markdown(
        """
<script>
(function(){
  var w = window.innerWidth || document.documentElement.clientWidth || 1024;
  var u = new URL(window.location.href);
  if (u.searchParams.get('_mw') !== String(w)) {
    u.searchParams.set('_mw', w);
    window.location.replace(u.toString());
  }
})();
</script>
""",
        unsafe_allow_html=True,
    )


def is_mobile() -> bool:
    """Retorna True se a tela for <= 768px.

    Detecta automaticamente via JS na primeira visita.
    Override: ?mobile=1 na URL força o modo mobile.
    """
    try:
        if str(st.query_params.get("mobile", "")).lower() in ("1", "true", "yes", "on"):
            return True
    except Exception:
        pass

    width = st.session_state.get(_SESSION_KEY)
    if width is not None:
        return int(width) <= _MOBILE_THRESHOLD

    return False


def detect_screen_width() -> None:
    """Detecta a largura da tela via JS. Chamar no início do app."""
    _inject_width_detector()
