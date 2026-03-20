"""Rate limiting para tentativas de login — proteção contra brute-force.

Estratégia: dois backends, selecionados automaticamente em runtime.

  1. Redis (preferencial) — persistente entre deploys e multi-instância.
     Ativado quando a variável de ambiente REDIS_URL estiver definida.
     Exemplo: REDIS_URL = "redis://localhost:6379/0"
             REDIS_URL = "rediss://user:pass@host:6380"  (TLS)

  2. Memória (fallback) — usa st.cache_resource para sobreviver a reruns
     dentro da mesma instância. O bloqueio é perdido ao reiniciar o
     processo (deploy, crash, scale-to-zero). Adequado apenas para
     Streamlit Cloud com instância única.

O código do app não precisa saber qual backend está ativo — a interface
pública (check_rate_limit / record_failure / record_success) é idêntica.

Limites padrão:
  - MAX_ATTEMPTS = 5 tentativas por janela
  - WINDOW_SECONDS = 300 (5 minutos)
  - LOCKOUT_SECONDS = 900 (15 minutos de bloqueio)

Uso:
    from src.auth.rate_limit import check_rate_limit, record_failure, record_success

    key = get_rate_limit_key(email)
    ok, msg, wait_secs = check_rate_limit(key)
    if not ok:
        st.error(msg)
        st.stop()

    if login_ok:
        record_success(key)
    else:
        record_failure(key)
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Protocol, Tuple

import streamlit as st

log = logging.getLogger("saas.rate_limit")

MAX_ATTEMPTS = 5
WINDOW_SECONDS = 300    # janela deslizante de 5 min
LOCKOUT_SECONDS = 900   # 15 min de bloqueio após esgotar tentativas

_REDIS_PREFIX = "rl:"
_REDIS_LOCK_SUFFIX = ":lock"
_REDIS_ATTEMPTS_SUFFIX = ":attempts"


# ── Protocol: interface comum aos backends ───────────────────────────────────

class _RateLimitBackend(Protocol):
    def check(self, key: str) -> Tuple[bool, str, int]: ...
    def record_failure(self, key: str) -> int: ...
    def record_success(self, key: str) -> None: ...
    def get_info(self, key: str) -> dict: ...


# ── Backend 1: Redis ─────────────────────────────────────────────────────────

class _RedisBackend:
    """Backend Redis — persistente entre deploys e multi-instância."""

    def __init__(self, redis_url: str) -> None:
        import redis as _redis
        self._client = _redis.from_url(redis_url, decode_responses=True)
        log.info("rate_limit: backend Redis inicializado (%s)", redis_url.split("@")[-1])

    def _lock_key(self, key: str) -> str:
        return f"{_REDIS_PREFIX}{key}{_REDIS_LOCK_SUFFIX}"

    def _attempts_key(self, key: str) -> str:
        return f"{_REDIS_PREFIX}{key}{_REDIS_ATTEMPTS_SUFFIX}"

    def check(self, key: str) -> Tuple[bool, str, int]:
        now = time.time()
        lock_val = self._client.get(self._lock_key(key))
        if lock_val is not None:
            locked_until = float(lock_val)
            if locked_until > now:
                wait = int(locked_until - now)
                mins, secs = wait // 60, wait % 60
                return (
                    False,
                    f"Conta temporariamente bloqueada por excesso de tentativas. "
                    f"Tente novamente em {mins}m {secs}s.",
                    wait,
                )
        cutoff = now - WINDOW_SECONDS
        pipe = self._client.pipeline()
        pipe.zremrangebyscore(self._attempts_key(key), "-inf", cutoff)
        pipe.zcard(self._attempts_key(key))
        _, count = pipe.execute()
        remaining = MAX_ATTEMPTS - count
        if remaining <= 0:
            locked_until = now + LOCKOUT_SECONDS
            self._client.set(self._lock_key(key), locked_until, ex=LOCKOUT_SECONDS)
            self._client.delete(self._attempts_key(key))
            mins = LOCKOUT_SECONDS // 60
            return (
                False,
                f"Muitas tentativas incorretas. Acesso bloqueado por {mins} minutos.",
                LOCKOUT_SECONDS,
            )
        return True, "", 0

    def record_failure(self, key: str) -> int:
        now = time.time()
        lock_val = self._client.get(self._lock_key(key))
        if lock_val is not None and float(lock_val) > now:
            return 0
        cutoff = now - WINDOW_SECONDS
        attempts_key = self._attempts_key(key)
        pipe = self._client.pipeline()
        pipe.zremrangebyscore(attempts_key, "-inf", cutoff)
        pipe.zadd(attempts_key, {str(now): now})
        pipe.zcard(attempts_key)
        pipe.expire(attempts_key, WINDOW_SECONDS + 10)
        _, _, count, _ = pipe.execute()
        remaining = MAX_ATTEMPTS - count
        if remaining <= 0:
            locked_until = now + LOCKOUT_SECONDS
            self._client.set(self._lock_key(key), locked_until, ex=LOCKOUT_SECONDS)
            self._client.delete(attempts_key)
            return 0
        return remaining

    def record_success(self, key: str) -> None:
        pipe = self._client.pipeline()
        pipe.delete(self._lock_key(key))
        pipe.delete(self._attempts_key(key))
        pipe.execute()

    def get_info(self, key: str) -> dict:
        now = time.time()
        cutoff = now - WINDOW_SECONDS
        attempts_key = self._attempts_key(key)
        lock_key = self._lock_key(key)
        pipe = self._client.pipeline()
        pipe.zremrangebyscore(attempts_key, "-inf", cutoff)
        pipe.zcard(attempts_key)
        pipe.get(lock_key)
        _, count, lock_val = pipe.execute()
        locked = lock_val is not None and float(lock_val) > now
        return {
            "key": key,
            "backend": "redis",
            "attempts_in_window": count,
            "locked": locked,
            "locked_until": float(lock_val) if locked else None,
        }


# ── Backend 2: Memória (fallback) ────────────────────────────────────────────

@dataclass
class _Bucket:
    attempts: list[float] = field(default_factory=list)
    locked_until: float = 0.0


@st.cache_resource()
def _get_memory_store() -> dict[str, _Bucket]:
    return {}


class _MemoryBackend:
    """Backend em memória — fallback quando Redis não está disponível.

    AVISO: o estado de bloqueio é perdido ao reiniciar o processo.
    Configure REDIS_URL para proteção real em produção.
    """

    def __init__(self) -> None:
        log.warning(
            "rate_limit: backend MEMÓRIA ativo. "
            "Configure REDIS_URL para persistência entre deploys."
        )

    def _bucket(self, key: str) -> _Bucket:
        store = _get_memory_store()
        if key not in store:
            store[key] = _Bucket()
        return store[key]

    def _clean_window(self, bucket: _Bucket, now: float) -> None:
        cutoff = now - WINDOW_SECONDS
        bucket.attempts = [t for t in bucket.attempts if t > cutoff]

    def check(self, key: str) -> Tuple[bool, str, int]:
        now = time.time()
        b = self._bucket(key)
        if b.locked_until > now:
            wait = int(b.locked_until - now)
            mins, secs = wait // 60, wait % 60
            return (
                False,
                f"Conta temporariamente bloqueada por excesso de tentativas. "
                f"Tente novamente em {mins}m {secs}s.",
                wait,
            )
        self._clean_window(b, now)
        remaining = MAX_ATTEMPTS - len(b.attempts)
        if remaining <= 0:
            b.locked_until = now + LOCKOUT_SECONDS
            b.attempts = []
            mins = LOCKOUT_SECONDS // 60
            return (
                False,
                f"Muitas tentativas incorretas. Acesso bloqueado por {mins} minutos.",
                LOCKOUT_SECONDS,
            )
        return True, "", 0

    def record_failure(self, key: str) -> int:
        now = time.time()
        b = self._bucket(key)
        if b.locked_until > now:
            return 0
        self._clean_window(b, now)
        b.attempts.append(now)
        remaining = MAX_ATTEMPTS - len(b.attempts)
        if remaining <= 0:
            b.locked_until = now + LOCKOUT_SECONDS
            b.attempts = []
            return 0
        return remaining

    def record_success(self, key: str) -> None:
        store = _get_memory_store()
        store.pop(key, None)

    def get_info(self, key: str) -> dict:
        now = time.time()
        b = self._bucket(key)
        self._clean_window(b, now)
        return {
            "key": key,
            "backend": "memory",
            "attempts_in_window": len(b.attempts),
            "locked": b.locked_until > now,
            "locked_until": b.locked_until if b.locked_until > now else None,
        }


# ── Seleção de backend ───────────────────────────────────────────────────────

@st.cache_resource()
def _get_backend() -> _RateLimitBackend:
    """Seleciona Redis se REDIS_URL estiver definida; caso contrário, memória."""
    redis_url = os.environ.get("REDIS_URL") or st.secrets.get("REDIS_URL", "")
    if redis_url:
        try:
            backend = _RedisBackend(redis_url)
            backend._client.ping()
            return backend
        except Exception as exc:
            log.error(
                "rate_limit: falha ao conectar ao Redis (%s). Usando memória. Erro: %s",
                redis_url.split("@")[-1], exc,
            )
    return _MemoryBackend()


# ── Interface pública ────────────────────────────────────────────────────────

def get_rate_limit_key(email: str) -> str:
    """Gera chave de rate limit normalizada a partir do e-mail."""
    return f"login:{(email or '').strip().lower()}"


def check_rate_limit(key: str) -> Tuple[bool, str, int]:
    """Verifica se a chave está bloqueada.

    Returns:
        (allowed, mensagem, segundos_restantes)
        allowed=True significa que a tentativa pode prosseguir.
    """
    return _get_backend().check(key)


def record_failure(key: str) -> int:
    """Registra falha de login. Retorna tentativas restantes."""
    return _get_backend().record_failure(key)


def record_success(key: str) -> None:
    """Limpa o bucket após login bem-sucedido."""
    _get_backend().record_success(key)


def get_attempts_info(key: str) -> dict:
    """Retorna info de diagnóstico sobre o bucket (para logs e healthcheck)."""
    return _get_backend().get_info(key)


def get_active_backend_name() -> str:
    """Retorna 'redis' ou 'memory' — útil para o healthcheck."""
    b = _get_backend()
    return "redis" if isinstance(b, _RedisBackend) else "memory"
