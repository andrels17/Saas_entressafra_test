import streamlit as st
from src.ui.pages.matriz_data import get_setores


def render_setores(ctx, selection):
    setores = get_setores(selection)

    if not setores:
        st.info("Nenhum setor encontrado.")
        return

    for setor in setores:
        with st.expander(f"📌 {setor.nome}", expanded=False):
            render_setor(setor, ctx)


def render_setor(setor, ctx):
    st.write(f"Renderizando setor: {setor.nome}")
