
import streamlit as st


def filter_shell():
    return st.container()


def filter_caption(text: str):
    st.markdown(
        f'<div class="ds-filter-caption">{text}</div>',
        unsafe_allow_html=True)


def filter_hint(text: str):
    st.caption(text)
