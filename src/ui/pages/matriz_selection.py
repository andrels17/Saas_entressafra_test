import streamlit as st


def render_selection(ctx):
    grupo = st.selectbox("Grupo", ["A", "B", "C"])
    departamento = st.selectbox("Departamento", ["X", "Y", "Z"])

    if not grupo or not departamento:
        return None

    return {
        "grupo": grupo,
        "departamento": departamento
    }
