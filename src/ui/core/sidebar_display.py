from __future__ import annotations

import streamlit as st
from typing import Optional, Tuple

from src.utils.supabase_helpers import sb_for_user


def _role_pretty(role: str) -> str:
    r = (role or "").strip().lower()
    mapping = {
        "admin":      "Admin",
        "superadmin": "Super Admin",
        "supervisor": "Supervisor",
        "gestor":     "Gestor",
        "user": "Usuário",
        "usuario": "Usuário",
        "membro": "Membro",
        "viewer": "Leitor",
    }
    return mapping.get(r, (role or "Usuário").capitalize())


def _first_nonempty(d: dict, keys: list[str]) -> Optional[str]:
    for k in keys:
        v = d.get(k) if isinstance(d, dict) else None
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


@st.cache_data(ttl=300, show_spinner=False)
def get_display_names(tenant_id: str, user_id: str) -> Tuple[str, str]:
    """Retorna (tenant_name, user_name) sem expor IDs.
    Robusto a schemas diferentes: tenta colunas comuns.
    """
    tenant_name = "Empresa"
    user_name = "Usuário"

    if not tenant_id or not user_id:
        return tenant_name, user_name

    sb = sb_for_user()

    # ── Tenant name ────────────────────────────────────────────────────
    # Tenta tenants.name, tenants.nome, tenants.titulo
    for col in ("name", "nome", "titulo", "company_name"):
        try:
            res = sb.table("tenants").select(col).eq("id", tenant_id).maybe_single().execute()
            if res and getattr(res, "data", None):
                tn = _first_nonempty(res.data, [col])
                if tn:
                    tenant_name = tn
                    break
        except Exception:
            continue

    # ── User name ──────────────────────────────────────────────────────
    # 1) user_profiles (tabela simples por user_id) — padrão do projeto
    #    public.user_profiles(user_id, nome)
    try:
        res = (
            sb.table("user_profiles")
            .select("nome")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        if res and getattr(res, "data", None):
            un = _first_nonempty(res.data, ["nome"])
            if un:
                user_name = un
    except Exception:
        pass

    # 2) tenant_users (multi-tenant) costuma ter 'nome' ou 'name'
    if user_name == "Usuário":
        for cols in (("nome",), ("name",), ("full_name",), ("email",), ("display_name",)):
            try:
                sel = ",".join(cols)
                res = (
                    sb.table("tenant_users")
                    .select(sel)
                    .eq("tenant_id", tenant_id)
                    .eq("user_id", user_id)
                    .maybe_single()
                    .execute()
                )
                if res and getattr(res, "data", None):
                    un = _first_nonempty(res.data, list(cols))
                    if un:
                        user_name = un
                        break
            except Exception:
                continue

    # 3) profiles (se existir)
    if user_name == "Usuário":
        for cols in (("nome",), ("name",), ("full_name",), ("email",), ("display_name",)):
            try:
                sel = ",".join(cols)
                res = sb.table("profiles").select(sel).eq("id", user_id).maybe_single().execute()
                if res and getattr(res, "data", None):
                    un = _first_nonempty(res.data, list(cols))
                    if un:
                        user_name = un
                        break
            except Exception:
                continue

    return tenant_name, user_name


def role_label(role: str) -> str:
    return _role_pretty(role)
