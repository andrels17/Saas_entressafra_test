"""Rate limit simples para tentativas de login.

Mantém compatibilidade com o app atual e com testes antigos.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import DefaultDict, List

import streamlit as st

# Armazena timestamps de falhas por chave
_STORE: DefaultDict[str, List[float]] = defaultdict(list)

# Janela e limite padrão
WINDOW_SECONDS = 15 * 60
MAX_FAILURES = 5


def _get_store():
    """Compatibilidade com testes legados."""
    return _STORE


def _bucket(key: str):
    """Compatibilidade com testes legados."""
    return _STORE.setdefault(key, [])


def _prune(key: str, now: float | None = None) -> List[float]:
    now = now if now is not None else time.time()
    limit = now - WINDOW_SECONDS
    bucket = [ts for ts in _STORE.get(key, []) if ts >= limit]
    _STORE[key] = bucket
    return bucket


def get_rate_limit_key(identifier: str | None = None) -> str:
    """Gera chave de rate limit por identificador ou sessão."""
    if identifier:
        return f"login:{str(identifier).strip().lower()}"

    session_user = (
        st.session_state.get("login_email")
        or st.session_state.get("user_email")
        or st.session_state.get("username")
        or "anonymous"
    )
    return f"login:{str(session_user).strip().lower()}"


def check_rate_limit(key: str, *, max_failures: int = MAX_FAILURES, window_seconds: int = WINDOW_SECONDS) -> bool:
    """Retorna True se a chave ainda pode tentar login."""
    global WINDOW_SECONDS
    previous_window = WINDOW_SECONDS
    WINDOW_SECONDS = window_seconds
    try:
        bucket = _prune(key)
        return len(bucket) < max_failures
    finally:
        WINDOW_SECONDS = previous_window


def record_failure(key: str) -> int:
    """Registra uma falha e retorna a quantidade de falhas válidas na janela."""
    bucket = _prune(key)
    bucket.append(time.time())
    _STORE[key] = bucket
    return len(bucket)


def record_success(key: str) -> None:
    """Limpa histórico de falhas após login bem-sucedido."""
    _STORE.pop(key, None)
