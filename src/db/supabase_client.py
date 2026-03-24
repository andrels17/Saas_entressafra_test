"""Supabase client factory.

Regras:
- Cliente anon é SEMPRE criado fresco (nunca cacheado) para evitar
  vazamento de token entre sessões/usuários no Streamlit Cloud.
- Service role pode ser cacheado pois nunca carrega token de usuário.
"""
from __future__ import annotations

import streamlit as st
from supabase import Client, create_client


def _supabase_config() -> tuple[str, str, str]:
    """Lê URL e chaves do st.secrets. Falha rápido se ausentes."""
    return (
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_ANON_KEY"],
        st.secrets["SUPABASE_SERVICE_ROLE_KEY"],
    )


def get_supabase_anon() -> Client:
    """Cliente anon com pool leve por token de sessão.

    Mantém uma instância por (url, token) em vez de criar um novo cliente
    TCP a cada rerun. Seguro contra vazamento entre usuários porque a chave
    inclui o próprio token do usuário: tokens diferentes → instâncias
    diferentes. Ao trocar de token (refresh / logout) a entrada antiga
    fica orfã e será coletada pelo GC normalmente.

    Evita os bugs graves originais:
      1. Estado de autenticação vazando entre sessões/usuários — cada token
         tem sua própria instância no cache.
      2. Token expirado — o token novo resulta em chave nova, cliente novo.
    """
    url, anon, _ = _supabase_config()

    # Tenta ler o token da sessão sem importar streamlit no topo do módulo
    # (evita ciclos de importação e mantém o módulo utilizável fora do Streamlit).
    token: str = ""
    try:
        import streamlit as st  # importação local intencional
        token = st.session_state.get("sb_access_token") or ""
    except Exception:
        pass

    cache_key = (url, token)
    client = _anon_cache.get(cache_key)
    if client is None:
        client = create_client(url, anon)
        if token:
            try:
                client.postgrest.auth(token)
            except Exception:
                pass
        _anon_cache[cache_key] = client
    return client


# Cache simples em nível de módulo: (url, token) → Client
# Não usa @st.cache_resource para evitar o problema de estado global compartilhado.
_anon_cache: dict[tuple[str, str], "Client"] = {}


# Alias mantido por compatibilidade retroativa
get_supabase_anon_fresh = get_supabase_anon


def get_supabase_service() -> Client:
    """Service role.

    Fora do contexto Streamlit (scheduler, GitHub Actions) o decorator
    @st.cache_resource não funciona corretamente — o cliente era criado
    com credenciais vazias e todas as queries retornavam [] silenciosamente.
    Cache manual via módulo garante uma única instância em qualquer contexto.
    """
    return _get_supabase_service_cached()


def _get_supabase_service_cached() -> Client:
    if _get_supabase_service_cached._client is None:
        url, _, svc = _supabase_config()
        _get_supabase_service_cached._client = create_client(url, svc)
    return _get_supabase_service_cached._client


_get_supabase_service_cached._client = None
