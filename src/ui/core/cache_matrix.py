import streamlit as st

from src.ui.core.cache import bump_data_version
from src.utils.kpi_engine import invalidate_kpi_cache


def invalidate_matriz_cache() -> None:
    """Invalida caches locais da Matriz e avança a versão global dos dados."""
    current = st.session_state.get("data_version", "0")
    try:
        st.session_state["data_version"] = str(int(float(current)) + 1)
    except Exception:
        st.session_state["data_version"] = "1"

    # também gera um novo token global para qualquer cache que dependa de data_version
    try:
        bump_data_version()
    except Exception:
        pass

    try:
        invalidate_kpi_cache()
    except Exception:
        pass

    for key in (
        "_mtz_payload_cache",
        "_mtz_group_ctx_cache",
        "_mtz_resumo_cache",
        "_mtz_prewarm_sig",
    ):
        st.session_state.pop(key, None)
