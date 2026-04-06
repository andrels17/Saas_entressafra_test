
import streamlit as st

from src.utils.supabase_helpers import sb_for_user, current_tenant_id, current_role
from src.ui.core.styles import page_header as _ph
from src.ui.core.design_system import inject_design_system_css
from src.ui.admin.equipamentos_tabs.import_csv import render_import_csv_tab
from src.ui.admin.equipamentos_tabs.organize import render_organize_tab
from src.ui.admin.equipamentos_tabs.audit import render_audit_tab


def render_admin_equipamentos() -> None:
    _ph("◫", "Equipamentos",
        "Importe frotas e organize em grupos. Remanejamento em lote suportado.")
    inject_design_system_css()

    role = current_role()
    if role not in ("admin", "superadmin"):
        st.error("Apenas Admin pode gerenciar equipamentos.")
        st.stop()

    tenant_id = current_tenant_id()

    # Usa sempre o cliente autenticado do usuário (RLS ativa).
    # O service_role foi removido daqui — bypass de RLS não é necessário
    # para operações de UI onde o usuário já é admin verificado pelo guard acima.
    sb = sb_for_user()

    tab1, tab2, tab3 = st.tabs(
        ["Importar CSV", "Organizar / Remanejar", "Histórico (Auditoria)"])
    with tab1:
        render_import_csv_tab(sb, tenant_id)
    with tab2:
        render_organize_tab(sb, tenant_id)
    with tab3:
        render_audit_tab(sb, tenant_id)
