"""Rate limiting para tentativas de login — proteção contra brute-force.

Estratégia: contador em memória por chave (IP ou e-mail).
No Streamlit Cloud cada processo tem sua própria memória, então esta
implementação usa st.cache_resource para persistir entre reruns da mesma
instância. Para ambientes multi-instância (k8s, etc.), substitua o
_store por Redis.

Limites padrão:
  - MAX_ATTEMPTS = 5 tentativas por janela
  - WINDOW_SECONDS = 300 (5 minutos)
  - LOCKOUT_SECONDS = 900 (15 minutos de bloqueio após esgotar tentativas)

Uso:
    from src.auth.rate_limit import check_rate_limit, record_failure, record_success

    key = get_rate_limit_key(email)
    ok, msg, wait_secs = check_rate_limit(key)
    if not ok:
        st.error(msg)
        st.stop()

    # ... tenta login ...

    if login_ok:
        record_success(key)
    else:
        record_failure(key)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Tuple

import streamlit as st

MAX_ATTEMPTS = 5
WINDOW_SECONDS = 300   # janela deslizante de 5 min
LOCKOUT_SECONDS = 900   # 15 min de bloqueio


@dataclass
class _Bucket:
    attempts: list[float] = field(
        default_factory=list)  # timestamps das falhas
    locked_until: float = 0.0


@st.cache_resource()
def _get_store() -> dict[str, _Bucket]:
    """Armazena buckets em cache_resource para sobreviver reruns."""
    return {}


def _bucket(key: str) -> _Bucket:
    store = _get_store()
    if key not in store:
        store[key] = _Bucket()
    return store[key]


def _clean_window(bucket: _Bucket, now: float) -> None:
    """Remove tentativas fora da janela deslizante."""
    cutoff = now - WINDOW_SECONDS
    bucket.attempts = [t for t in bucket.attempts if t > cutoff]


def get_rate_limit_key(email: str) -> str:
    """Gera chave de rate limit normalizada a partir do e-mail."""
    return f"login:{(email or '').strip().lower()}"


def check_rate_limit(key: str) -> Tuple[bool, str, int]:
    """Verifica se a chave está bloqueada.

    Returns:
        (allowed, mensagem, segundos_restantes)
        allowed=True significa que a tentativa pode prosseguir.
    """
    now = time.time()
    b = _bucket(key)

    if b.locked_until > now:
        wait = int(b.locked_until - now)
        mins = wait // 60
        secs = wait % 60
        return (
            False,
            f"Conta temporariamente bloqueada por excesso de tentativas. "
            f"Tente novamente em {mins}m {secs}s.",
            wait,
        )

    _clean_window(b, now)
    remaining = MAX_ATTEMPTS - len(b.attempts)

    if remaining <= 0:
        b.locked_until = now + LOCKOUT_SECONDS
        b.attempts = []
        wait = LOCKOUT_SECONDS
        mins = wait // 60
        return (
            False,
            f"Muitas tentativas incorretas. Acesso bloqueado por {mins} minutos.",
            wait,
        )

    return True, "", 0


def record_failure(key: str) -> int:
    """Registra falha de login. Retorna tentativas restantes."""
    now = time.time()
    b = _bucket(key)

    if b.locked_until > now:
        return 0

    _clean_window(b, now)
    b.attempts.append(now)

    remaining = MAX_ATTEMPTS - len(b.attempts)
    if remaining <= 0:
        b.locked_until = now + LOCKOUT_SECONDS
        b.attempts = []
        return 0

    return remaining


def record_success(key: str) -> None:
    """Limpa o bucket após login bem-sucedido."""
    store = _get_store()
    store.pop(key, None)


def get_attempts_info(key: str) -> dict:
    """Retorna info de diagnóstico sobre o bucket (para logs)."""
    now = time.time()
    b = _bucket(key)
    _clean_window(b, now)
    return {
        "key": key,
        "attempts_in_window": len(b.attempts),
        "locked": b.locked_until > now,
        "locked_until": b.locked_until if b.locked_until > now else None,
    }
