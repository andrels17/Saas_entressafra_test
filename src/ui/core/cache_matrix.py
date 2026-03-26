import streamlit as st

from src.ui.core.cache import bump_data_version
from src.utils.kpi_engine import invalidate_kpi_cache


def invalidate_matriz_cache() -> None:
    """Invalida caches locais da Matriz e força refresh global dos KPIs."""
    bump_data_version()
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
