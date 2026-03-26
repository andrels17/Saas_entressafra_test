"""Helpers de acesso ao Supabase para o contexto de usuário logado.

Fonte única de verdade para:
  - Criar um cliente autenticado como o usuário atual (sb_for_user)
  - Ler tenant_id / role / user_id do session_state de forma segura
  - Fornecer helpers puros que não quebram testes quando `supabase`
    não está instalado
"""

from __future__ import annotations

from typing import Any

import streamlit as st

try:
    from supabase import Client  # type: ignore
except ModuleNotFoundError:
    class Client:  # type: ignore
        """Fallback apenas para tipagem quando supabase não estiver instalado."""
        pass

from src.db.supabase_client import get_supabase_anon


def sb_for_user() -> Client:
    """Retorna cliente Supabase autenticado como o usuário atual.

    Cria um cliente fresco a cada chamada para evitar vazamento de estado
    entre sessões em ambiente multi-usuário (Streamlit Cloud).
    """
    sb = get_supabase_anon()
    token = st.session_state.get("sb_access_token")
    if token:
        try:
            sb.postgrest.auth(token)
        except Exception:
            # Mantém compatibilidade com versões/stubs diferentes do cliente
            pass
    return sb


def current_tenant_id() -> str:
    """Retorna tenant_id atual ou levanta RuntimeError se não selecionado."""
    tid = st.session_state.get("current_tenant_id")
    if not tid:
        raise RuntimeError("Tenant não selecionado.")
    return str(tid).strip()


def current_role() -> str:
    """Retorna role atual do usuário (string vazia se não definido)."""
    return str(st.session_state.get("current_role") or "").strip()


def current_user_id() -> str:
    """Retorna user_id do usuário logado (string vazia se não definido)."""
    return str(st.session_state.get("sb_user_id") or "").strip()


def normalize_id(value: Any) -> str:
    """Normaliza qualquer ID (UUID, int, str) para string."""
    if value is None:
        return ""
    return str(value).strip()


def sanitize_user_input(value: str, *, max_length: int = 500) -> str:
    """Sanitiza entrada do usuário antes de usar em queries ou atualizações."""
    if not value:
        return ""

    allowed_controls = {"\n", "\t"}
    cleaned = "".join(
        c for c in str(value)
        if c in allowed_controls or (ord(c) >= 32 and ord(c) != 127)
    )
    return cleaned.strip()[:max_length]
