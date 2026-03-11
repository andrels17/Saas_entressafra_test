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
    """Cliente anon fresco por chamada — nunca cacheado.

    Evita dois bugs graves do cache_resource:
      1. Estado de autenticação vazando entre sessões/usuários.
      2. Token expirado travado no cliente cacheado após reboot.
    """
    url, anon, _ = _supabase_config()
    return create_client(url, anon)


# Alias mantido por compatibilidade retroativa
get_supabase_anon_fresh = get_supabase_anon


@st.cache_resource
def get_supabase_service() -> Client:
    """Service role: seguro para cache pois não carrega token de usuário."""
    url, _, svc = _supabase_config()
    return create_client(url, svc)
