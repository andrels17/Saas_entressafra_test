"""Detecção automática de mobile via JavaScript — sem toggle manual.

Estratégia:
  1. Um snippet JS mede window.innerWidth e recarrega com ?_mw=<largura>.
  2. is_mobile() lê ?_mw direto dos query_params — funciona já no primeiro
     rerun após o redirect, sem depender de session_state.
  3. Override: ?mobile=1 força o modo mobile (atalho para campo).

Threshold: <= 768px = mobile.
"""
from __future__ import annotations

import streamlit as st

_MOBILE_THRESHOLD = 768
_SESSION_KEY = "__screen_width__"


def _inject_width_detector() -> None:
    """Injeta JS que mede a largura e recarrega com ?_mw=<largura> se necessário."""
    if st.session_state.get("__width_detected__"):
        return

    try:
        mw = st.query_params.get("_mw")
        if mw and str(mw).isdigit():
            st.session_state[_SESSION_KEY] = int(mw)
            st.session_state["__width_detected__"] = True
            return
    except Exception:
        pass

    # Primeira visita: injeta JS que mede e redireciona
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

    Lê ?_mw= dos query_params diretamente — funciona no mesmo rerun
    em que o JS redireciona, sem depender de session_state.
    """
    try:
        if str(st.query_params.get("mobile", "")).lower() in ("1", "true", "yes", "on"):
            return True
    except Exception:
        pass

    # Lê direto da URL — disponível imediatamente após o redirect do JS
    try:
        mw = st.query_params.get("_mw")
        if mw and str(mw).isdigit():
            return int(mw) <= _MOBILE_THRESHOLD
    except Exception:
        pass

    # Fallback: session_state (gravado em visitas anteriores)
    width = st.session_state.get(_SESSION_KEY)
    if width is not None:
        return int(width) <= _MOBILE_THRESHOLD

    return False


def detect_screen_width() -> None:
    """Detecta a largura da tela via JS. Chamar no início do app."""
    _inject_width_detector()
