
import streamlit as st

from src.ui.admin.equipamentos_tabs.organize_sections.ativos import render_ativos_section
from src.ui.admin.equipamentos_tabs.organize_sections.lixeira import render_lixeira_section
from src.ui.admin.equipamentos_tabs.organize_sections.mover import render_mover_section
from src.ui.admin.equipamentos_tabs.organize_sections.limpeza import render_limpeza_section


def render_organize_tab(sb, tenant_id: str) -> None:
    st.caption("Inclui edição inline, lixeira (restaurar) e auditoria.")

    subt1, subt2, subt3, subt4 = st.tabs([
        "Ativos (editar em tabela)",
        "Lixeira (restaurar)",
        "Mover / Edição individual",
        "Limpeza",
    ])

    with subt1:
        render_ativos_section(sb, tenant_id)

    with subt2:
        render_lixeira_section(sb, tenant_id)

    with subt3:
        render_mover_section(sb, tenant_id)

    with subt4:
        render_limpeza_section(sb, tenant_id)
