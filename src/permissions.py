"""Guards de autenticação e autorização.

Refatorado para usar Role enum ao invés de strings mágicas.
Interface pública preservada para compatibilidade.
"""
import streamlit as st
from src.auth.roles import Role
from src.auth.session import is_logged_in


def require_login():
    if not is_logged_in():
        st.warning("Faça login para continuar.")
        st.stop()


def require_role(*roles: str):
    """Bloqueia acesso se o role atual não estiver na lista permitida."""
    current = (st.session_state.get("current_role") or "").strip()
    allowed = {r.strip() for r in roles if r}
    if allowed and current not in allowed:
        st.error("Você não tem permissão para acessar esta página.")
        st.stop()


def require_tenant_selected():
    if not st.session_state.get("current_tenant_id"):
        st.error("Nenhum tenant selecionado.")
        st.stop()


def is_admin() -> bool:
    """Verifica se o usuário atual tem role de admin/superadmin."""
    return Role.is_admin(st.session_state.get("current_role"))


def is_manager() -> bool:
    """Verifica se o usuário tem role de gestor ou superior."""
    return Role.is_manager(st.session_state.get("current_role"))
