
import streamlit as st


def render_global_filters(revisoes, departamentos, grupos, equipamentos):
    """Barra de filtros global"""

    with st.container():
        st.markdown("### 🔎 Filtros")

        col1, col2, col3, col4, col5 = st.columns(5)

        with col1:
            revisao = st.selectbox("Revisão", revisoes)

        with col2:
            departamento = st.selectbox(
                "Departamento", ["Todos"] + departamentos)

        with col3:
            grupo = st.selectbox("Grupo", ["Todos"] + grupos)

        with col4:
            equipamento = st.selectbox("Equipamento", ["Todos"] + equipamentos)

        with col5:
            search = st.text_input("Pesquisa global")

    return {
        "revisao": revisao,
        "departamento": departamento,
        "grupo": grupo,
        "equipamento": equipamento,
        "search": search,
    }
