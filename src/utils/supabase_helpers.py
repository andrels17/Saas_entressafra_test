"""Helpers de acesso ao Supabase para o contexto de usuário logado.

Fonte única de verdade para:
  - Criar um cliente autenticado como o usuário atual (sb_for_user)
  - Ler tenant_id / role / user_id do session_state de forma segura
"""

try:
    from supabase import Client  # type: ignore
except ModuleNotFoundError as e:
    raise ModuleNotFoundError(
        "Biblioteca supabase não instalada. "
        "Adicione `supabase` no requirements.txt e faça redeploy."
    ) from e

import streamlit as st

from src.db.supabase_client import get_supabase_anon


def sb_for_user() -> Client:
    """Retorna cliente Supabase autenticado como o usuário atual.

    Cria um cliente fresco a cada chamada para evitar vazamento de estado
    entre sessões em ambiente multi-usuário (Streamlit Cloud).
    """
    sb = get_supabase_anon()
    token = st.session_state.get("sb_access_token")
    if token:
        sb.postgrest.auth(token)
    return sb


def current_tenant_id() -> str:
    """Retorna tenant_id atual ou levanta RuntimeError se não selecionado."""
    tid = st.session_state.get("current_tenant_id")
    if not tid:
        raise RuntimeError("Tenant não selecionado.")
    return tid


def current_role() -> str:
    """Retorna role atual do usuário (string vazia se não definido)."""
    return st.session_state.get("current_role") or ""


def current_user_id() -> str:
    """Retorna user_id do usuário logado (string vazia se não definido)."""
    return st.session_state.get("sb_user_id") or ""


def normalize_id(value) -> str:
    """Normaliza qualquer ID (UUID, int, str) para string.

    Garante consistência nas comparações de chaves entre dados do Supabase
    que podem retornar tipos diferentes dependendo da coluna (UUID vs int).
    """
    if value is None:
        return ""
    return str(value).strip()

def sanitize_user_input(value: str, *, max_length: int = 500) -> str:
    """Sanitiza entrada do usuário antes de usar em queries ou atualizações.

    - Remove caracteres de controle (exceto newline/tab)
    - Limita comprimento
    - Strip de espaços extras

    PostgREST parametriza valores automaticamente (sem SQL injection direta),
    mas esta função protege contra dados malformados e excessivamente longos.
    """
    if not value:
        return ""
    # Remove null bytes e outros caracteres de controle problemáticos
    allowed_controls = {"\n", "\t"}
    cleaned = "".join(
        c for c in value
        if c in allowed_controls or (ord(c) >= 32 and ord(c) != 127)
    )
    return cleaned.strip()[:max_length]
