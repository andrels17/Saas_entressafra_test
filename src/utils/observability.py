"""Observabilidade — logging estruturado e rastreamento de erros.

Substitui os 200+ blocos `except Exception: pass` espalhados pelo código
por um padrão único que:
  1. Sempre loga o erro com contexto (módulo, função, tabela, tenant)
  2. Retorna o valor default seguro ([] ou None) para não quebrar a UI
  3. Expõe métricas de erro via st.cache_resource para diagnóstico

Uso básico:
    from src.utils.observability import log_error, capture

    # Substitui: except Exception: pass
    except Exception as exc:
        log_error(exc, context="equipamentos.load", table="equipamentos")

    # Substitui um try/except inteiro retornando default:
    rows = capture(lambda: sb.table("x").select("*").execute().data or [],
                   default=[], context="my_query")

Integração futura com Sentry:
    Adicione SENTRY_DSN em st.secrets e descomente a seção Sentry abaixo.
    O módulo detecta automaticamente e roteia erros para lá.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, TypeVar

import streamlit as st

# ── Logger raiz do projeto ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("saas")

T = TypeVar("T")

# ── Sentry (opcional) ───────────────────────────────────────────────────
# Para ativar: adicione SENTRY_DSN em st.secrets e instale sentry-sdk
_sentry_ready = False


def _init_sentry() -> bool:
    try:
        dsn = st.secrets.get("SENTRY_DSN", "")
        if not dsn:
            return False
        import sentry_sdk  # type: ignore
        sentry_sdk.init(
            dsn=dsn,
            traces_sample_rate=0.1,
            environment=st.secrets.get("APP_ENV", "production"),
        )
        log.info("Sentry inicializado.")
        return True
    except Exception:
        return False


def _maybe_capture_sentry(exc: Exception, context: dict) -> None:
    global _sentry_ready
    if not _sentry_ready:
        _sentry_ready = _init_sentry()
    if not _sentry_ready:
        return
    try:
        import sentry_sdk  # type: ignore
        with sentry_sdk.push_scope() as scope:
            for k, v in context.items():
                scope.set_extra(k, v)
            sentry_sdk.capture_exception(exc)
    except Exception:
        pass


# ── Registro em memória (últimos 200 erros) ─────────────────────────────
@dataclass
class ErrorRecord:
    ts: float
    level: str
    context: str
    exc_type: str
    message: str
    tenant_id: str | None = None


# Mantido em nível de módulo para funcionar tanto no app real quanto nos testes
# unitários, onde streamlit.cache_resource é apenas um stub sem memoização.
_ERROR_RING: deque[ErrorRecord] = deque(maxlen=200)


@st.cache_resource()
def _error_ring() -> deque[ErrorRecord]:
    """Ring buffer de erros, compartilhado entre reruns da mesma instância."""
    return _ERROR_RING


def get_recent_errors(n: int = 50) -> list[ErrorRecord]:
    """Retorna os N erros mais recentes (mais novo primeiro)."""
    ring = _error_ring()
    items = list(ring)
    items.reverse()
    return items[:n]


def get_error_count_since(seconds: int = 300) -> int:
    """Conta erros nos últimos `seconds` segundos."""
    cutoff = time.time() - seconds
    return sum(1 for e in _error_ring() if e.ts >= cutoff)


# ── Função principal de log ─────────────────────────────────────────────

def log_error(
    exc: Exception,
    *,
    context: str = "",
    table: str | None = None,
    tenant_id: str | None = None,
    level: str = "warning",
    extra: dict | None = None,
) -> None:
    """Registra um erro com contexto estruturado.

    Args:
        exc: A exceção capturada.
        context: Identificador legível do ponto de falha, ex: "kpi_engine._fetch_mv".
        table: Nome da tabela Supabase envolvida (ajuda no diagnóstico).
        tenant_id: Tenant afetado. Lido do session_state se omitido.
        level: "debug" | "info" | "warning" | "error" | "critical"
        extra: Dados adicionais para o log estruturado.
    """
    resolved_tenant = tenant_id or (
        st.session_state.get("current_tenant_id") if _has_session() else None
    )

    ctx: dict[str, Any] = {
        "context": context,
        "exc_type": type(exc).__name__,
        "tenant_id": resolved_tenant,
    }
    if table:
        ctx["table"] = table
    if extra:
        ctx.update(extra)

    msg = f"{context} | {type(exc).__name__}: {exc}"
    if table:
        msg = f"[{table}] {msg}"

    logger = logging.getLogger(f"saas.{context}" if context else "saas")
    log_fn = getattr(logger, level, logger.warning)
    log_fn(msg)

    # Rastreia no ring buffer (diagnóstico na UI)
    _error_ring().append(ErrorRecord(
        ts=time.time(),
        level=level,
        context=context,
        exc_type=type(exc).__name__,
        message=str(exc)[:300],
        tenant_id=resolved_tenant,
    ))

    # Envia para Sentry se disponível
    _maybe_capture_sentry(exc, ctx)


def _has_session() -> bool:
    """Verifica se o session_state está disponível (evita erros fora do Streamlit)."""
    try:
        _ = st.session_state
        return True
    except Exception:
        return False


# ── capture: wrapper funcional para safe-defaults ────────────────────────────

def capture(
    fn: Callable[[], T],
    *,
    default: T,
    context: str = "",
    table: str | None = None,
    tenant_id: str | None = None,
    level: str = "warning",
) -> T:
    """Executa `fn()` e retorna `default` em caso de exceção, sempre logando.

    Substitui o padrão:
        try:
            return fn()
        except Exception:
            return []

    Por:
        return capture(fn, default=[], context="meu_modulo.minha_func", table="equipamentos")

    Args:
        fn: Callable sem argumentos que executa a operação.
        default: Valor retornado se fn() lançar qualquer exceção.
        context: Identificador do ponto de falha para logs.
        table: Tabela Supabase envolvida.
        tenant_id: Tenant afetado.
        level: Nível de severidade do log.

    Returns:
        Resultado de fn() ou default em caso de falha.
    """
    try:
        return fn()
    except Exception as exc:
        log_error(
            exc,
            context=context,
            table=table,
            tenant_id=tenant_id,
            level=level)
        return default


# ── Decorator para funções de repositório ────────────────────────────────────

def safe_query(context: str, table: str | None = None, default: Any = None):
    """Decorator que envolve uma função de query com log de erro automático.

    Uso:
        @safe_query("kpi_engine._fetch_mv", table="mv_revisao_grupo_kpis", default=[])
        def _fetch_mv(tenant_id, revisao_id):
            ...
    """
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                log_error(exc, context=context, table=table)
                return default if default is not None else []
        return wrapper
    return decorator


# ── Widget de diagnóstico para admins ───────────────────────────────────

def render_error_diagnostics() -> None:
    """Renderiza painel de diagnóstico de erros recentes (apenas para admins).

    Adicione em qualquer página admin:
        from src.utils.observability import render_error_diagnostics
        render_error_diagnostics()
    """
    try:
        role = str(st.session_state.get("current_role", "")).lower()
        if role not in ("admin", "superadmin"):
            return
    except Exception:
        return

    recent = get_recent_errors(50)
    count_5m = get_error_count_since(300)

    with st.expander(f"🔍 Diagnóstico de erros ({count_5m} nos últimos 5 min)", expanded=False):
        if not recent:
            st.success("Nenhum erro recente registrado.")
            return

        import pandas as pd
        df = pd.DataFrame([{
            "horário": time.strftime("%H:%M:%S", time.localtime(e.ts)),
            "nível": e.level,
            "contexto": e.context,
            "tipo": e.exc_type,
            "mensagem": e.message[:80],
        } for e in recent])
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption(
            f"Mostrando os {len(recent)} erros mais recentes desta instância."
        )
