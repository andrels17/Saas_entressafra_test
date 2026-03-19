"""Gerenciamento de tenant selecionado e role do usuário.

Responsabilidades:
  - Carregar lista de tenants do usuário logado
  - Garantir que exista um tenant selecionado (ou exibir seletor)
  - Revalidar o role no banco a cada run para evitar escalada de privilégio
"""
from __future__ import annotations

import streamlit as st

from src.db.supabase_client import get_supabase_anon
from src.ui.core.setup_tenant import render_setup_tenant

# Ordem de precedência de roles (maior índice = mais permissivo)
_ROLE_RANK: dict[str, int] = {
    "viewer": 0,
    "executor": 1,
    "gestor": 2,
    "admin": 3,
    "superadmin": 4,
}


# ── Helpers privados ────────────────────────────────────────────────────

def _current_user_id() -> str:
    return st.session_state.get(
        "sb_user_id") or st.session_state.get("user_id") or ""


def _clear_tenant_selection() -> None:
    """Limpa tenant/role para evitar herdar escopo entre usuários."""
    for k in ("current_tenant_id", "current_role", "_tenant_user_id"):
        st.session_state.pop(k, None)


# ── Funções públicas ────────────────────────────────────────────────────

def refresh_current_role() -> str:
    """Revalida o role no banco para o usuário atual e tenant selecionado.

    Returns:
        Role validado (string) ou vazio se não encontrado.
    """
    tenant_id = st.session_state.get("current_tenant_id")
    user_id = _current_user_id()
    token = st.session_state.get("sb_access_token")

    if not (tenant_id and user_id and token):
        return ""

    sb = get_supabase_anon()
    sb.postgrest.auth(token)

    try:
        res = (
            sb.table("tenant_users")
            .select("role")
            .eq("tenant_id", tenant_id)
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        role = ((getattr(res, "data", None) or {}).get(
            "role") or "viewer").lower()
    except Exception:
        role = "viewer"

    st.session_state["current_role"] = role
    st.session_state["_tenant_user_id"] = user_id
    return role


def load_user_tenants() -> list[dict]:
    """Carrega tenants do usuário logado, deduplicando por role mais permissivo."""
    sb = get_supabase_anon()
    sb.postgrest.auth(st.session_state["sb_access_token"])

    rows = (sb.table("tenant_users").select(
        "tenant_id, role, tenants(nome)").execute().data) or []

    # Deduplicação: mesmo tenant pode ter múltiplos vínculos — mantém o mais
    # permissivo
    by_tenant: dict[str, tuple[int, dict]] = {}
    for r in rows:
        tid = r.get("tenant_id")
        if not tid:
            continue
        role = (r.get("role") or "viewer").lower()
        rank = _ROLE_RANK.get(role, 0)
        prev_rank, _ = by_tenant.get(tid, (-1, None))
        if rank > prev_rank:
            by_tenant[tid] = (rank, r)

    tenants = [v[1] for v in by_tenant.values()]
    tenants.sort(
        key=lambda x: (
            (x.get("tenants") or {}).get("nome") or "").lower())
    return tenants


_ROLE_REVALIDATION_TTL = 120  # segundos entre revalidações de role no banco


def ensure_tenant_selected() -> None:
    """Garante que exista um tenant selecionado em session_state.

    Proteções anti-vazamento:
    - Tenant/role ficam vinculados ao user_id atual.
    - Se o user_id mudar, limpamos tenant/role para não herdar escopo.
    - Role é revalidado no banco a cada _ROLE_REVALIDATION_TTL segundos
      (em vez de todo rerun), reduzindo queries sem sacrificar segurança.
    """
    import time
    user_id = _current_user_id()
    prev_user = st.session_state.get("_tenant_user_id")

    # Usuário trocou: não herdar tenant/role anteriores
    if prev_user and user_id and prev_user != user_id:
        _clear_tenant_selection()

    # Tenant já selecionado: revalida role apenas se TTL expirou
    if st.session_state.get("current_tenant_id"):
        now = time.time()
        last_val = st.session_state.get("_role_last_validated", 0)
        if now - last_val >= _ROLE_REVALIDATION_TTL:
            if refresh_current_role():
                st.session_state["_role_last_validated"] = now
                return
            _clear_tenant_selection()
        else:
            return

    tenants = load_user_tenants()

    if not tenants:
        render_setup_tenant()
        st.stop()

    if len(tenants) == 1:
        st.session_state["current_tenant_id"] = tenants[0]["tenant_id"]
        st.session_state["current_role"] = tenants[0]["role"]
        return

    # Múltiplos tenants: exibe seletor
    st.markdown("## Selecione a empresa")
    options = {f'{t["tenants"]["nome"]} ({t["role"]})': t for t in tenants}
    choice = st.selectbox("Empresa", list(options.keys()))

    if st.button("Continuar", use_container_width=True, type="primary"):
        t = options[choice]
        st.session_state["current_tenant_id"] = t["tenant_id"]
        st.session_state["current_role"] = t["role"]
        st.rerun()

    st.info("Selecione a empresa e clique em **Continuar**.")
    st.stop()
