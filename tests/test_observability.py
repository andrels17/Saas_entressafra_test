"""Testes unitários para src/utils/observability.py.

Executar com: pytest tests/test_observability.py -v
Não requer Supabase, rede nem Streamlit.
"""
from __future__ import annotations

import sys
import types
import time
from collections import deque
from unittest.mock import patch, MagicMock

import pytest

# ── Stub de streamlit ─────────────────────────────────────────────────────────
class _CacheResource:
    def __call__(self, func=None, **kw):
        if func is not None:
            return func
        return lambda f: f

if "streamlit" not in sys.modules:
    _st = types.ModuleType("streamlit")
    _st.cache_resource = _CacheResource()
    _st.session_state = {}
    sys.modules["streamlit"] = _st

import streamlit as st

sys.path.insert(0, ".")
import src.utils.observability as obs


# ── Fixture: ring buffer isolado por teste ────────────────────────────────────
@pytest.fixture(autouse=True)
def reset_ring():
    st.session_state.clear()
    ring = obs._error_ring()
    ring.clear()
    yield
    ring.clear()


# ─────────────────────────────────────────────────────────────────────────────
# log_error
# ─────────────────────────────────────────────────────────────────────────────

def test_log_error_registra_no_ring():
    exc = ValueError("algo deu errado")
    obs.log_error(exc, context="test.modulo", table="tabela_x")
    assert len(obs._error_ring()) == 1
    record = obs._error_ring()[-1]
    assert record.exc_type == "ValueError"
    assert record.context == "test.modulo"


def test_log_error_nao_propaga_excecao():
    """log_error jamais deve lançar exceção."""
    exc = RuntimeError("boom")
    obs.log_error(exc, context="test")  # não deve explodir


def test_log_error_resolve_session_state():
    st.session_state["current_tenant_id"] = "tid-abc"
    exc = Exception("err")
    obs.log_error(exc, context="ctx")
    record = obs._error_ring()[-1]
    assert record.tenant_id == "tid-abc"


def test_log_error_tenant_explicito_sobrepoe_session():
    st.session_state["current_tenant_id"] = "tid-session"
    exc = Exception("err")
    obs.log_error(exc, context="ctx", tenant_id="tid-explicito")
    record = obs._error_ring()[-1]
    assert record.tenant_id == "tid-explicito"


def test_log_error_acumula_multiplos():
    for i in range(5):
        obs.log_error(Exception(f"erro {i}"), context=f"ctx.{i}")
    assert len(obs._error_ring()) == 5


def test_log_error_ring_respeita_maxlen():
    """Ring buffer não deve crescer além do limite (200)."""
    for i in range(250):
        obs.log_error(Exception(f"e{i}"), context="ctx")
    assert len(obs._error_ring()) <= 200


# ─────────────────────────────────────────────────────────────────────────────
# get_recent_errors
# ─────────────────────────────────────────────────────────────────────────────

def test_get_recent_errors_retorna_lista():
    obs.log_error(Exception("x"), context="c")
    errors = obs.get_recent_errors(10)
    assert isinstance(errors, list)
    assert len(errors) == 1


def test_get_recent_errors_mais_novo_primeiro():
    obs.log_error(Exception("primeiro"), context="a")
    time.sleep(0.01)
    obs.log_error(Exception("segundo"), context="b")
    errors = obs.get_recent_errors()
    assert errors[0].context == "b"
    assert errors[1].context == "a"


def test_get_recent_errors_respeita_n():
    for i in range(20):
        obs.log_error(Exception(f"e{i}"), context="c")
    errors = obs.get_recent_errors(5)
    assert len(errors) == 5


def test_get_recent_errors_vazio():
    assert obs.get_recent_errors() == []


# ─────────────────────────────────────────────────────────────────────────────
# get_error_count_since
# ─────────────────────────────────────────────────────────────────────────────

def test_get_error_count_since_conta_recentes():
    obs.log_error(Exception("x"), context="c")
    obs.log_error(Exception("y"), context="c")
    assert obs.get_error_count_since(60) == 2


def test_get_error_count_since_ignora_antigos():
    ring = obs._error_ring()
    # Injeta registro antigo manualmente
    old_record = obs.ErrorRecord(
        ts=time.time() - 400,
        level="warning",
        context="old",
        exc_type="Exception",
        message="antigo",
    )
    ring.append(old_record)
    obs.log_error(Exception("recente"), context="c")
    assert obs.get_error_count_since(300) == 1  # só o recente


def test_get_error_count_since_zero_se_vazio():
    assert obs.get_error_count_since(60) == 0


# ─────────────────────────────────────────────────────────────────────────────
# capture
# ─────────────────────────────────────────────────────────────────────────────

def test_capture_retorna_resultado_normal():
    result = obs.capture(lambda: [1, 2, 3], default=[], context="test")
    assert result == [1, 2, 3]


def test_capture_retorna_default_em_excecao():
    def boom():
        raise ValueError("falhou")

    result = obs.capture(boom, default=[], context="test.capture")
    assert result == []


def test_capture_loga_excecao():
    obs.capture(lambda: 1 / 0, default=0, context="divisao")
    assert len(obs._error_ring()) == 1
    assert obs._error_ring()[-1].exc_type == "ZeroDivisionError"


def test_capture_nao_propaga_excecao():
    result = obs.capture(
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        default="safe",
        context="test",
    )
    assert result == "safe"


def test_capture_default_none():
    result = obs.capture(lambda: 1 / 0, default=None, context="test")
    assert result is None


def test_capture_default_dict():
    result = obs.capture(lambda: 1 / 0, default={}, context="test")
    assert result == {}


# ─────────────────────────────────────────────────────────────────────────────
# safe_query decorator
# ─────────────────────────────────────────────────────────────────────────────

def test_safe_query_decorator_sucesso():
    @obs.safe_query("test.fn", table="tb", default=[])
    def my_fn():
        return [{"id": 1}]

    result = my_fn()
    assert result == [{"id": 1}]


def test_safe_query_decorator_retorna_default():
    @obs.safe_query("test.fn2", table="tb2", default=[])
    def my_fn2():
        raise ConnectionError("sem rede")

    result = my_fn2()
    assert result == []


def test_safe_query_decorator_loga_erro():
    @obs.safe_query("test.fn3", default=[])
    def my_fn3():
        raise RuntimeError("db error")

    my_fn3()
    assert len(obs._error_ring()) == 1
    assert obs._error_ring()[-1].context == "test.fn3"


def test_safe_query_preserva_nome_funcao():
    @obs.safe_query("ctx", default=None)
    def minha_funcao_importante():
        pass

    assert minha_funcao_importante.__name__ == "minha_funcao_importante"
