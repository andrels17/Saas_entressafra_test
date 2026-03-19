
import streamlit as st
import pandas as pd


def page_header(title: str, subtitle: str | None = None):
    """Header padrão para páginas"""
    st.title(title)
    if subtitle:
        st.caption(subtitle)
    st.divider()


def section_header(title: str):
    """Header de seção"""
    st.markdown(f"### {title}")


def success_message(msg: str):
    st.success(msg)


def error_message(msg: str):
    st.error(msg)


def info_message(msg: str):
    st.info(msg)


def kpi_card(label: str, value, delta=None):
    """Card padrão de KPI"""
    col = st.container()
    with col:
        st.metric(label=label, value=value, delta=delta)


def dataframe_table(df: pd.DataFrame, height: int = 400):
    """Tabela padronizada"""
    st.dataframe(
        df,
        use_container_width=True,
        height=height,
    )


def action_buttons(label: str, key: str):
    """Botão padrão de ação"""
    return st.button(label, key=key, use_container_width=True)
