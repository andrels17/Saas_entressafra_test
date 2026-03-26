import streamlit as st


def invalidate_matriz_cache() -> None:
    """Invalida caches locais da Matriz e avança a versão global dos dados."""
    try:
        from src.ui.core.cache import bump_data_version

        bump_data_version()
    except Exception:
        current = st.session_state.get("data_version", "0")
        try:
            current = int(float(current))
        except Exception:
            current = 0
        st.session_state["data_version"] = str(current + 1)

    for key in (
        "_mtz_payload_cache",
        "_mtz_group_ctx_cache",
        "_mtz_resumo_cache",
        "_mtz_prewarm_sig",
    ):
        st.session_state.pop(key, None)

    try:
        from src.utils.kpi_engine import invalidate_kpi_cache

        invalidate_kpi_cache()
    except Exception:
        pass
