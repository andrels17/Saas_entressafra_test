
from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st


def data_table(
    df: pd.DataFrame,
    *,
    column_config: dict[str, Any] | None = None,
    height: int | None = None,
    hide_index: bool = True,
    use_container_width: bool = True,
) -> None:
    """Tabela padrão da aplicação com defaults consistentes."""
    st.dataframe(
        df,
        use_container_width=use_container_width,
        hide_index=hide_index,
        height=height,
        column_config=column_config or {},
    )


def titled_table(
    title: str,
    df: pd.DataFrame,
    *,
    caption: str | None = None,
    empty_message: str = "Sem dados para exibir.",
    column_config: dict[str, Any] | None = None,
    height: int | None = None,
    hide_index: bool = True,
    use_container_width: bool = True,
) -> None:
    """Renderiza título, caption opcional e tabela padronizada."""
    st.markdown(f"### {title}")
    if df.empty:
        st.info(empty_message)
        return
    if caption:
        st.caption(caption)
    data_table(
        df,
        column_config=column_config,
        height=height,
        hide_index=hide_index,
        use_container_width=use_container_width,
    )
