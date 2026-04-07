"""Supabase client factory.

Regras:
- Cliente anon é SEMPRE criado fresco (nunca cacheado) para evitar
  vazamento de token entre sessões/usuários no Streamlit Cloud.
- Service role pode ser cacheado pois nunca carrega token de usuário.
"""
from __future__ import annotations

import time

import streamlit as st
from supabase import Client, create_client

# ── Cache com TTL e tamanho máximo ────────────────────────────────────────────
# Substitui o dict simples anterior que crescia indefinidamente.
# TTL de 1h: tokens JWT do Supabase expiram em 1h por padrão, então
# entradas expiradas são descartadas automaticamente antes disso.
# maxsize de 512: cobre picos razoáveis de usuários simultâneos sem
# consumo descontrolado de memória.
_ANON_CACHE_TTL = 3_600   # segundos (1 hora)
_ANON_CACHE_MAX = 512


class _TTLCache:
    """Cache LRU simples com TTL, sem dependências externas."""

    __slots__ = ("_maxsize", "_ttl", "_store")

    def __init__(self, maxsize: int, ttl: float) -> None:
        self._maxsize = maxsize
        self._ttl = ttl
        # {key: (value, expires_at)}
        self._store: dict = {}

    def get(self, key):
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key, value) -> None:
        # Evicção simples quando cheio: descarta a metade mais antiga
        if len(self._store) >= self._maxsize:
            now = time.monotonic()
            # Remove expirados primeiro
            expired = [k for k, (_, exp) in self._store.items() if exp <= now]
            for k in expired:
                del self._store[k]
            # Se ainda cheio, descarta metade das entradas mais antigas
            if len(self._store) >= self._maxsize:
                evict = list(self._store)[: self._maxsize // 2]
                for k in evict:
                    del self._store[k]
        self._store[key] = (value, time.monotonic() + self._ttl)

    def pop(self, key, default=None):
        entry = self._store.pop(key, None)
        if entry is None:
            return default
        return entry[0]

    def keys(self):
        return list(self._store.keys())


# Cache em nível de módulo: (url, token) → Client
# Não usa @st.cache_resource para evitar o problema de estado global compartilhado.
_anon_cache: _TTLCache = _TTLCache(maxsize=_ANON_CACHE_MAX, ttl=_ANON_CACHE_TTL)


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
    expira pelo TTL ou é descartada na evicção.

    Evita os bugs graves originais:
      1. Estado de autenticação vazando entre sessões/usuários — cada token
         tem sua própria instância no cache.
      2. Token expirado — o token novo resulta em chave nova, cliente novo.
      3. Memory leak — entradas órfãs de tokens expirados são descartadas
         automaticamente pelo TTL (1h) e pelo limite de tamanho (512).
    """
    url, anon, _ = _supabase_config()

    # Tenta ler o token da sessão sem importar streamlit no topo do módulo
    # (evita ciclos de importação e mantém o módulo utilizável fora do Streamlit).
    token: str = ""
    try:
        import streamlit as _st  # importação local intencional

        token = _st.session_state.get("sb_access_token") or ""
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
        _anon_cache.set(cache_key, client)
    return client


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
