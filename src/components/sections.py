
import streamlit as st


def section_header(title: str, caption: str = ""):
    st.markdown(
        f'''<div class="ds-section"><div class="ds-section__title">{title}</div><div class="ds-section__caption">{caption}</div></div>''',
        unsafe_allow_html=True)


def soft_divider():
    st.markdown('<div class="ds-divider"></div>', unsafe_allow_html=True)
