"""Admin • Usuários — orquestrador das 3 abas."""
import streamlit as st

from src.db.supabase_client import get_supabase_service
from src.utils.supabase_helpers import current_tenant_id, current_role
from src.utils import nav
from src.ui.core.styles import page_header as _ph

from src.ui.admin.usuarios_tabs.criar import render_tab_criar
from src.ui.admin.usuarios_tabs.gerenciar import render_tab_gerenciar, _load_tenant_users
from src.ui.admin.usuarios_tabs.permissoes import render_tab_permissoes


def _rerun():
    try:
        nav.rerun_keep_menu()
    except Exception:
        st.rerun()


def _safe_json(e):
    try:
        return e.json()
    except Exception:
        return {"message": str(e)}


def render_admin_usuarios():
    _ph("⊹", "Usuários", "Crie e gerencie usuários (sem convite), roles e permissões por setor.")

    tenant_id = current_tenant_id()
    role = current_role()

    if role not in ("admin", "superadmin"):
        st.error("Apenas Admin pode gerenciar usuários.")
        st.stop()

    svc = get_supabase_service()

    tab_create, tab_manage, tab_perms = st.tabs(
        ["Criar usuário (senha direta)", "Gerenciar usuários", "Permissões por setor"]
    )

    with tab_create:
        render_tab_criar(svc, tenant_id, _rerun, _safe_json)

    with tab_manage:
        render_tab_gerenciar(svc, tenant_id, _rerun, _safe_json)

    with tab_perms:
        users = _load_tenant_users(svc, tenant_id)
        render_tab_permissoes(svc, tenant_id, users, _rerun, _safe_json)
