"""Rate limit para tentativas de login.

Backend adaptativo:
- Se REDIS_URL estiver configurado em st.secrets → usa Redis (persistido entre workers)
- Caso contrário → fallback em memória (funcional mas não cross-worker)

Configuração para produção multitenante (recomendado):
    # secrets.toml
    REDIS_URL = "redis://default:senha@host:6379"

Interface pública:
    check_rate_limit(key) -> (allowed, message, wait_secs)
    record_failure(key)
    record_success(key)
"""
from __future__ import annotations

import math
import time
from collections import defaultdict
from typing import DefaultDict

import streamlit as st

WINDOW_SECONDS = 15 * 60
MAX_FAILURES   = 5

# ── Backend em memória (fallback) ─────────────────────────────────────────────
_STORE: DefaultDict[str, list[float]] = defaultdict(list)


def _get_store():
    return _STORE


def _bucket(key: str):
    return _STORE.setdefault(key, [])


def _prune(key: str, now: float | None = None,
           *, window_seconds: int = WINDOW_SECONDS) -> list[float]:
    now = now if now is not None else time.time()
    cutoff = now - window_seconds
    bucket = [ts for ts in _STORE.get(key, []) if ts >= cutoff]
    _STORE[key] = bucket
    return bucket


# ── Backend Redis (produção) ───────────────────────────────────────────────────

def _get_redis():
    """Tenta obter cliente Redis. Retorna None se não configurado ou falhar."""
    try:
        redis_url = st.secrets.get("REDIS_URL") or ""
        if not redis_url:
            return None
        import redis  # type: ignore
        client = redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
        client.ping()
        return client
    except Exception:
        return None


def _redis_check(key: str, *, max_failures: int, window_seconds: int) -> tuple[bool, str, int]:
    """Rate limit via Redis usando sorted set."""
    r = _get_redis()
    if r is None:
        return _memory_check(key, max_failures=max_failures, window_seconds=window_seconds)
    try:
        now = time.time()
        cutoff = now - window_seconds
        pipe = r.pipeline()
        pipe.zremrangebyscore(key, "-inf", cutoff)
        pipe.zcard(key)
        pipe.expire(key, window_seconds)
        _, count, _ = pipe.execute()

        if count < max_failures:
            return True, "", 0

        oldest = r.zrange(key, 0, 0, withscores=True)
        wait_secs = max(0, int(math.ceil(
            float(oldest[0][1]) + window_seconds - now
        ))) if oldest else window_seconds
        msg = (
            f"Muitas tentativas de login. Tente novamente em {wait_secs} "
            f"segundo{'s' if wait_secs != 1 else ''}."
        )
        return False, msg, wait_secs
    except Exception:
        # Redis falhou — fallback em memória
        return _memory_check(key, max_failures=max_failures, window_seconds=window_seconds)


def _redis_record_failure(key: str) -> int:
    r = _get_redis()
    if r is None:
        return _memory_record_failure(key)
    try:
        now = time.time()
        pipe = r.pipeline()
        pipe.zadd(key, {str(now): now})
        pipe.expire(key, WINDOW_SECONDS)
        pipe.zcard(key)
        results = pipe.execute()
        return int(results[2])
    except Exception:
        return _memory_record_failure(key)


def _redis_record_success(key: str) -> None:
    r = _get_redis()
    if r is None:
        _memory_record_success(key)
        return
    try:
        r.delete(key)
    except Exception:
        _memory_record_success(key)


# ── Backend memória ────────────────────────────────────────────────────────────

def _memory_check(key: str, *, max_failures: int,
                  window_seconds: int) -> tuple[bool, str, int]:
    now = time.time()
    bucket = _prune(key, now, window_seconds=window_seconds)
    if len(bucket) < max_failures:
        return True, "", 0
    oldest_relevant = bucket[0]
    wait_secs = max(0, int(math.ceil((oldest_relevant + window_seconds) - now)))
    msg = (
        f"Muitas tentativas de login. Tente novamente em {wait_secs} "
        f"segundo{'s' if wait_secs != 1 else ''}."
    )
    return False, msg, wait_secs


def _memory_record_failure(key: str) -> int:
    bucket = _prune(key)
    bucket.append(time.time())
    _STORE[key] = bucket
    return len(bucket)


def _memory_record_success(key: str) -> None:
    _STORE.pop(key, None)


# ── Interface pública ──────────────────────────────────────────────────────────

def get_rate_limit_key(identifier: str | None = None) -> str:
    if identifier:
        return f"login:{str(identifier).strip().lower()}"
    session_user = (
        st.session_state.get("login_email")
        or st.session_state.get("user_email")
        or st.session_state.get("username")
        or "anonymous"
    )
    return f"login:{str(session_user).strip().lower()}"


def check_rate_limit(
    key: str,
    *,
    max_failures: int = MAX_FAILURES,
    window_seconds: int = WINDOW_SECONDS,
) -> tuple[bool, str, int]:
    """Retorna (allowed, message, wait_secs).

    Usa Redis se disponível, memória caso contrário.
    """
    return _redis_check(key, max_failures=max_failures, window_seconds=window_seconds)


def record_failure(key: str) -> int:
    return _redis_record_failure(key)


def record_success(key: str) -> None:
    _redis_record_success(key)
