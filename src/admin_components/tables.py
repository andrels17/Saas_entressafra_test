
import pandas as pd
import streamlit as st


def clean_table(
        df: pd.DataFrame,
        cols: list[str] | None = None,
        height: int = 380):
    if df is None or df.empty:
        st.info("Sem dados para exibir.")
        return
    view = df.copy()
    if cols:
        keep = [c for c in cols if c in view.columns]
        if keep:
            view = view[keep]
    st.dataframe(
        view,
        use_container_width=True,
        hide_index=True,
        height=height)


def stats_strip(items: list[tuple[str, str]]):
    """Renders a horizontal strip of (label, value) metric pairs."""
    if not items:
        return
    cols = st.columns(len(items), border=True)
    for col, (label, value) in zip(cols, items):
        with col:
            st.metric(label=label, value=value)
