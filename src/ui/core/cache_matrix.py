import streamlit as st


def invalidate_matriz_cache() -> None:
    """Invalida caches locais da Matriz e avança a versão dos dados."""
    current = st.session_state.get("data_version", 0)

    try:
        current = int(float(current))
    except (TypeError, ValueError):
        current = 0

    st.session_state["data_version"] = str(current + 1)

    for key in (
        "_mtz_payload_cache",
        "_mtz_group_ctx_cache",
        "_mtz_resumo_cache",
        "_mtz_prewarm_sig",
    ):
        st.session_state.pop(key, None)
