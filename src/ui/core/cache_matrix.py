import streamlit as st

from src.ui.core.cache import bump_data_version
from src.utils.kpi_engine import invalidate_kpi_cache


def invalidate_matriz_cache() -> None:
    """Invalida caches locais da Matriz e avança a versão global dos dados.

    Ordem de operações:
      1. invalidate_kpi_cache — limpa @st.cache_data de KPIs e incrementa
         data_version + _kpi_ver em um único passo coordenado.
      2. bump_data_version — garante um novo token de tempo para quaisquer
         outros caches que dependam de data_version mas não de _kpi_ver.
      3. Limpa chaves de session_state da Matriz.
    """
    # 1. Invalida KPIs (já incrementa data_version internamente)
    try:
        invalidate_kpi_cache()
    except Exception:
        pass

    # 2. Gera um novo token de tempo (sem incrementar data_version de novo)
    try:
        bump_data_version()
    except Exception:
        pass

    # 3. Remove caches de session_state da Matriz
    for key in (
        "_mtz_payload_cache",
        "_mtz_group_ctx_cache",
        "_mtz_resumo_cache",
        "_mtz_prewarm_sig",
    ):
        st.session_state.pop(key, None)
