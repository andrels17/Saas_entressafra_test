"""Rate limit simples para tentativas de login.

Compatível com o app atual:
- check_rate_limit(key) -> (allowed, message, wait_secs)
e também com testes legados:
- _get_store()
- _bucket(key)
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from typing import DefaultDict

import streamlit as st

_STORE: DefaultDict[str, list[float]] = defaultdict(list)

WINDOW_SECONDS = 15 * 60
MAX_FAILURES = 5


def _get_store():
    return _STORE


def _bucket(key: str):
    return _STORE.setdefault(key, [])


def _prune(key: str, now: float | None = None, *, window_seconds: int = WINDOW_SECONDS) -> list[float]:
    now = now if now is not None else time.time()
    cutoff = now - window_seconds
    bucket = [ts for ts in _STORE.get(key, []) if ts >= cutoff]
    _STORE[key] = bucket
    return bucket


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
    """Retorna (allowed, message, wait_secs)."""
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


def record_failure(key: str) -> int:
    bucket = _prune(key)
    bucket.append(time.time())
    _STORE[key] = bucket
    return len(bucket)


def record_success(key: str) -> None:
    _STORE.pop(key, None)
