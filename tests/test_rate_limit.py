"""Testes unitários para src/auth/rate_limit.py.

Executar com: pytest tests/test_rate_limit.py -v
Não requer Supabase, Streamlit nem rede.

Como o módulo usa st.cache_resource para persistência, usamos
monkeypatch para injetar um dicionário isolado por teste.
"""
from __future__ import annotations

import time
import pytest

# ── Stub mínimo de streamlit para rodar fora do contexto Streamlit ────────────
import sys
import types

if "streamlit" not in sys.modules:
    _st = types.ModuleType("streamlit")
    _st.cache_resource = lambda **kw: (lambda f: f)
    _st.session_state = {}
    sys.modules["streamlit"] = _st
else:
    import streamlit as _st
    _st.session_state = getattr(_st, "session_state", {})


# ── Patch: substitui _get_store por uma factory que retorna dict isolado ──────
import importlib

import src.auth.rate_limit as rl


def _make_fresh_store():
    """Retorna novo dict vazio e injeta no módulo para isolamento."""
    fresh: dict = {}
    rl._get_store = lambda: fresh   # type: ignore[attr-defined]
    return fresh


# ─────────────────────────────────────────────────────────────────────────────
# get_rate_limit_key
# ─────────────────────────────────────────────────────────────────────────────

def test_key_normaliza_email():
    assert rl.get_rate_limit_key("USER@EMPRESA.COM") == "login:user@empresa.com"

def test_key_strip_espacos():
    assert rl.get_rate_limit_key("  joao@x.com  ") == "login:joao@x.com"

def test_key_email_vazio():
    assert rl.get_rate_limit_key("") == "login:"


# ─────────────────────────────────────────────────────────────────────────────
# check_rate_limit — estado inicial
# ─────────────────────────────────────────────────────────────────────────────

def test_nova_chave_permitida():
    _make_fresh_store()
    allowed, msg, wait = rl.check_rate_limit("login:novo@x.com")
    assert allowed is True
    assert msg == ""
    assert wait == 0


def test_check_sem_falhas_permitido():
    _make_fresh_store()
    key = "login:a@b.com"
    allowed, _, _ = rl.check_rate_limit(key)
    assert allowed is True


# ─────────────────────────────────────────────────────────────────────────────
# record_failure e bloqueio progressivo
# ─────────────────────────────────────────────────────────────────────────────

def test_primeira_falha_retorna_restantes():
    _make_fresh_store()
    key = "login:x@y.com"
    remaining = rl.record_failure(key)
    assert remaining == rl.MAX_ATTEMPTS - 1


def test_falhas_acumulam():
    _make_fresh_store()
    key = "login:acum@y.com"
    for i in range(3):
        rl.record_failure(key)
    remaining = rl.record_failure(key)
    assert remaining == rl.MAX_ATTEMPTS - 4


def test_esgotar_tentativas_bloqueia():
    _make_fresh_store()
    key = "login:block@y.com"
    for _ in range(rl.MAX_ATTEMPTS):
        rl.record_failure(key)
    allowed, msg, wait = rl.check_rate_limit(key)
    assert allowed is False
    assert wait > 0
    assert "bloqueado" in msg.lower() or "bloqueada" in msg.lower()


def test_bloqueio_retorna_zero_remaining():
    _make_fresh_store()
    key = "login:zero@y.com"
    # Exaure todas as tentativas
    for _ in range(rl.MAX_ATTEMPTS):
        rl.record_failure(key)
    # Mais uma após bloqueio — não deve explodir
    remaining = rl.record_failure(key)
    assert remaining == 0


# ─────────────────────────────────────────────────────────────────────────────
# record_success — limpa o bucket
# ─────────────────────────────────────────────────────────────────────────────

def test_sucesso_limpa_contador():
    store = _make_fresh_store()
    key = "login:clean@y.com"
    rl.record_failure(key)
    rl.record_failure(key)
    rl.record_success(key)
    assert key not in store


def test_apos_sucesso_permite_novamente():
    _make_fresh_store()
    key = "login:retry@y.com"
    rl.record_failure(key)
    rl.record_success(key)
    allowed, _, _ = rl.check_rate_limit(key)
    assert allowed is True


def test_sucesso_em_chave_inexistente_nao_quebra():
    _make_fresh_store()
    rl.record_success("login:naoexiste@y.com")  # não deve levantar exceção


# ─────────────────────────────────────────────────────────────────────────────
# Janela deslizante (time-based)
# ─────────────────────────────────────────────────────────────────────────────

def test_tentativas_expiradas_nao_contam(monkeypatch):
    """Falhas fora da janela de WINDOW_SECONDS são ignoradas."""
    _make_fresh_store()
    key = "login:expire@y.com"

    # Injeta falhas com timestamp muito antigo (fora da janela)
    old_ts = time.time() - rl.WINDOW_SECONDS - 10
    bucket = rl._bucket(key)
    bucket.attempts = [old_ts] * (rl.MAX_ATTEMPTS - 1)

    allowed, _, _ = rl.check_rate_limit(key)
    assert allowed is True  # tentativas antigas foram limpas


def test_lockout_expira_com_tempo(monkeypatch):
    """Após LOCKOUT_SECONDS, o bloqueio deve ser liberado."""
    _make_fresh_store()
    key = "login:unlock@y.com"

    # Força bloqueio com locked_until no passado
    bucket = rl._bucket(key)
    bucket.locked_until = time.time() - 1  # já expirou

    allowed, _, _ = rl.check_rate_limit(key)
    assert allowed is True


# ─────────────────────────────────────────────────────────────────────────────
# get_attempts_info — diagnóstico
# ─────────────────────────────────────────────────────────────────────────────

def test_attempts_info_chave_nova():
    _make_fresh_store()
    info = rl.get_attempts_info("login:info@y.com")
    assert info["attempts_in_window"] == 0
    assert info["locked"] is False


def test_attempts_info_apos_falhas():
    _make_fresh_store()
    key = "login:info2@y.com"
    rl.record_failure(key)
    rl.record_failure(key)
    info = rl.get_attempts_info(key)
    assert info["attempts_in_window"] == 2
    assert info["locked"] is False


def test_attempts_info_bloqueado():
    _make_fresh_store()
    key = "login:infoblk@y.com"
    for _ in range(rl.MAX_ATTEMPTS):
        rl.record_failure(key)
    info = rl.get_attempts_info(key)
    assert info["locked"] is True
    assert info["locked_until"] is not None
