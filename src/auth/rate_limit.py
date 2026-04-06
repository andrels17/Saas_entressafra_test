"""Rate limit para tentativas de login com backend em memória e fallback Redis."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict

import streamlit as st

WINDOW_SECONDS = 15 * 60
LOCKOUT_SECONDS = WINDOW_SECONDS
MAX_ATTEMPTS = 5
MAX_FAILURES = MAX_ATTEMPTS  # compatibilidade


@dataclass
class _Bucket:
    attempts: list[float] = field(default_factory=list)
    locked_until: float | None = None


def _get_memory_store() -> Dict[str, _Bucket]:
    global _STORE
    try:
        return _STORE
    except NameError:
        _STORE = {}
        return _STORE


def _get_store():
    return _get_memory_store()


def _coerce_bucket(value) -> _Bucket:
    if isinstance(value, _Bucket):
        return value
    if isinstance(value, list):
        return _Bucket(attempts=list(value), locked_until=None)
    return _Bucket()


def _bucket(key: str) -> _Bucket:
    store = _get_store()
    bucket = _coerce_bucket(store.get(key))
    store[key] = bucket
    return bucket


def _prune_bucket(bucket: _Bucket, now: float | None = None, *, window_seconds: int = WINDOW_SECONDS) -> _Bucket:
    now = time.time() if now is None else now
    cutoff = now - window_seconds
    bucket.attempts = [ts for ts in bucket.attempts if ts >= cutoff]
    if bucket.locked_until is not None and bucket.locked_until <= now:
        bucket.locked_until = None
    return bucket


class _MemoryBackend:
    def _bucket(self, key: str) -> _Bucket:
        bucket = _bucket(key)
        return _prune_bucket(bucket)

    def check(self, key: str, *, max_failures: int = MAX_ATTEMPTS, window_seconds: int = WINDOW_SECONDS) -> tuple[bool, str, int]:
        now = time.time()
        bucket = _prune_bucket(_bucket(key), now, window_seconds=window_seconds)
        if bucket.locked_until is not None and bucket.locked_until > now:
            wait_secs = max(0, int(math.ceil(bucket.locked_until - now)))
            msg = f"Login bloqueado temporariamente. Tente novamente em {wait_secs} segundo{'s' if wait_secs != 1 else ''}."
            return False, msg, wait_secs
        if len(bucket.attempts) >= max_failures:
            oldest = bucket.attempts[0]
            wait_secs = max(0, int(math.ceil(oldest + window_seconds - now)))
            if wait_secs > 0:
                msg = f"Login bloqueado temporariamente. Tente novamente em {wait_secs} segundo{'s' if wait_secs != 1 else ''}."
                return False, msg, wait_secs
        return True, "", 0

    def record_failure(self, key: str) -> int:
        now = time.time()
        bucket = _prune_bucket(_bucket(key), now)
        if bucket.locked_until is not None and bucket.locked_until > now:
            return 0
        bucket.attempts.append(now)
        if len(bucket.attempts) >= MAX_ATTEMPTS:
            bucket.locked_until = now + LOCKOUT_SECONDS
            return 0
        return max(0, MAX_ATTEMPTS - len(bucket.attempts))

    def record_success(self, key: str) -> None:
        _get_store().pop(key, None)

    def get_info(self, key: str) -> dict:
        now = time.time()
        bucket = _prune_bucket(_bucket(key), now)
        locked_until = bucket.locked_until if bucket.locked_until and bucket.locked_until > now else None
        return {
            "attempts_in_window": len(bucket.attempts),
            "remaining": max(0, MAX_ATTEMPTS - len(bucket.attempts)),
            "locked": locked_until is not None,
            "locked_until": locked_until,
        }


@st.cache_resource
def _get_redis_client():
    try:
        redis_url = st.secrets.get("REDIS_URL") or ""
        if not redis_url:
            return None
        import redis  # type: ignore

        client = redis.from_url(
            redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
            retry_on_timeout=True,
            health_check_interval=30,
        )
        client.ping()
        return client
    except Exception:
        return None


def _get_redis():
    try:
        return _get_redis_client()
    except Exception:
        return None


def _memory_backend() -> _MemoryBackend:
    return _MemoryBackend()


def _memory_check(key: str, *, max_failures: int, window_seconds: int) -> tuple[bool, str, int]:
    return _memory_backend().check(key, max_failures=max_failures, window_seconds=window_seconds)


def _memory_record_failure(key: str) -> int:
    return _memory_backend().record_failure(key)


def _memory_record_success(key: str) -> None:
    _memory_backend().record_success(key)


def _redis_check(key: str, *, max_failures: int, window_seconds: int) -> tuple[bool, str, int]:
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
        wait_secs = max(0, int(math.ceil(float(oldest[0][1]) + window_seconds - now))) if oldest else window_seconds
        msg = f"Login bloqueado temporariamente. Tente novamente em {wait_secs} segundo{'s' if wait_secs != 1 else ''}."
        return False, msg, wait_secs
    except Exception:
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
        count = int(results[2])
        return max(0, MAX_ATTEMPTS - count)
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


def get_rate_limit_key(identifier: str | None = None) -> str:
    if identifier is not None:
        return f"login:{str(identifier).strip().lower()}"
    session_user = (
        st.session_state.get("login_email")
        or st.session_state.get("user_email")
        or st.session_state.get("username")
        or ""
    )
    return f"login:{str(session_user).strip().lower()}"


def check_rate_limit(key: str, *, max_failures: int = MAX_FAILURES, window_seconds: int = WINDOW_SECONDS) -> tuple[bool, str, int]:
    return _redis_check(key, max_failures=max_failures, window_seconds=window_seconds)


def record_failure(key: str) -> int:
    return _redis_record_failure(key)


def record_success(key: str) -> None:
    _redis_record_success(key)


def get_attempts_info(key: str) -> dict:
    return _memory_backend().get_info(key)
