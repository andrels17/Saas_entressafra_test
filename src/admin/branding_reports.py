"""Admin • Branding & Relatórios — orquestrador dos painéis."""
import streamlit as st

from src.ui.core.styles import page_header
from src.utils.supabase_helpers import sb_for_user, current_tenant_id

from src.ui.admin.branding_tabs.branding_panel import render_tab_branding
from src.ui.admin.branding_tabs.relatorio_panel import render_tab_relatorio


def render_admin_branding_reports():
    page_header(
        "Branding & Relatórios",
        "White-label do tenant e Relatório Executivo PDF (foco em evolução e % concluído)",
    )

    sb = sb_for_user()
    tenant_id = current_tenant_id()

    col1, col2 = st.columns([1.15, 1], gap="large")

    with col1:
        branding = render_tab_branding(sb, tenant_id)

    with col2:
        render_tab_relatorio(sb, tenant_id, branding)
