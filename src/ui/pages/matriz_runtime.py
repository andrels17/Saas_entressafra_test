import streamlit as st


def get_runtime_context():
    if "matriz_ctx" not in st.session_state:
        st.session_state.matriz_ctx = {}

    return st.session_state.matriz_ctx
