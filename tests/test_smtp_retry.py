"""Testes unitários para src/services/email/smtp_sender.py — foco no retry.

Executar com: pytest tests/test_smtp_retry.py -v
Não requer SMTP real nem rede.
"""
from __future__ import annotations

import smtplib
import sys
import time
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ── Stubs ─────────────────────────────────────────────────────────────────────
class _CR:
    def __call__(self, f=None, **kw): return f if f else (lambda fn: fn)

if "streamlit" not in sys.modules:
    st = types.ModuleType("streamlit")
    st.cache_resource = _CR()
    st.session_state = {}
    sys.modules["streamlit"] = st

# Stub observability para não precisar de streamlit completo
if "src.utils.observability" not in sys.modules:
    obs = types.ModuleType("src.utils.observability")
    obs.log_error = lambda *a, **kw: None
    sys.modules["src.utils.observability"] = obs

# Stub timezone
if "src.utils.timezone" not in sys.modules:
    tz = types.ModuleType("src.utils.timezone")
    tz.fmt_brt = lambda fmt: "01/01/2025"
    sys.modules["src.utils.timezone"] = tz

sys.path.insert(0, ".")

from src.services.email.smtp_sender import (
    SmtpConfig,
    EmailMessage,
    send_email_with_retry,
    _send_once,
    _PERMANENT_ERRORS,
    _TRANSIENT_ERRORS,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def cfg():
    return SmtpConfig(
        host="smtp.test.com",
        port=587,
        user="test@test.com",
        password="secret",
        max_retries=3,
        retry_base=0.001,  # backoff mínimo para testes rápidos
    )


@pytest.fixture
def msg():
    return EmailMessage(
        to=["dest@test.com"],
        subject="Teste",
        html_body="<p>Teste</p>",
    )


# ── send_email_with_retry — sucesso na primeira tentativa ─────────────────────

def test_retry_sucesso_primeira_tentativa(cfg, msg):
    with patch("src.services.email.smtp_sender._send_once") as mock_send:
        send_email_with_retry(msg, cfg=cfg)
    mock_send.assert_called_once()


def test_retry_nao_chama_on_retry_em_sucesso(cfg, msg):
    on_retry = MagicMock()
    with patch("src.services.email.smtp_sender._send_once"):
        send_email_with_retry(msg, cfg=cfg, on_retry=on_retry)
    on_retry.assert_not_called()


# ── Retry em erros transitórios ───────────────────────────────────────────────

def test_retry_tenta_3_vezes_em_erro_transitorio(cfg, msg):
    with patch("src.services.email.smtp_sender._send_once") as mock_send:
        mock_send.side_effect = ConnectionRefusedError("sem conexão")
        with pytest.raises(ConnectionRefusedError):
            send_email_with_retry(msg, cfg=cfg)
    assert mock_send.call_count == cfg.max_retries


def test_retry_sucesso_na_segunda_tentativa(cfg, msg):
    call_count = [0]
    def side_effect(*a, **kw):
        call_count[0] += 1
        if call_count[0] < 2:
            raise ConnectionRefusedError("falhou")

    with patch("src.services.email.smtp_sender._send_once", side_effect=side_effect):
        send_email_with_retry(msg, cfg=cfg)  # não deve lançar
    assert call_count[0] == 2


def test_retry_sucesso_na_terceira_tentativa(cfg, msg):
    call_count = [0]
    def side_effect(*a, **kw):
        call_count[0] += 1
        if call_count[0] < 3:
            raise TimeoutError("timeout")

    with patch("src.services.email.smtp_sender._send_once", side_effect=side_effect):
        send_email_with_retry(msg, cfg=cfg)
    assert call_count[0] == 3


def test_retry_chama_on_retry_callback(cfg, msg):
    retries = []
    def on_retry(attempt, exc):
        retries.append(attempt)

    with patch("src.services.email.smtp_sender._send_once") as mock_send:
        mock_send.side_effect = [ConnectionRefusedError("err"), None]
        send_email_with_retry(msg, cfg=cfg, on_retry=on_retry)
    assert retries == [1]


def test_retry_on_retry_exception_nao_propaga(cfg, msg):
    """Exceção dentro do on_retry não deve interromper o fluxo."""
    def bad_callback(attempt, exc):
        raise RuntimeError("callback explodiu")

    with patch("src.services.email.smtp_sender._send_once") as mock_send:
        mock_send.side_effect = [ConnectionRefusedError("err"), None]
        send_email_with_retry(msg, cfg=cfg, on_retry=bad_callback)  # não deve explodir


# ── Erros permanentes — sem retry ─────────────────────────────────────────────

def test_retry_nao_retentar_erro_auth(cfg, msg):
    """SMTPAuthenticationError é permanente — deve falhar imediatamente."""
    with patch("src.services.email.smtp_sender._send_once") as mock_send:
        mock_send.side_effect = smtplib.SMTPAuthenticationError(535, b"Auth failed")
        with pytest.raises(smtplib.SMTPAuthenticationError):
            send_email_with_retry(msg, cfg=cfg)
    assert mock_send.call_count == 1  # apenas 1 tentativa


def test_retry_nao_retentar_destinatario_recusado(cfg, msg):
    with patch("src.services.email.smtp_sender._send_once") as mock_send:
        mock_send.side_effect = smtplib.SMTPRecipientsRefused({"dest@test.com": (550, b"User unknown")})
        with pytest.raises(smtplib.SMTPRecipientsRefused):
            send_email_with_retry(msg, cfg=cfg)
    assert mock_send.call_count == 1


# ── Configuração de retry ─────────────────────────────────────────────────────

def test_retry_respeita_max_retries():
    cfg_2 = SmtpConfig(
        host="h", port=587, user="u", password="p",
        max_retries=2, retry_base=0.001,
    )
    msg_local = EmailMessage(to=["a@b.com"], subject="s", html_body="h")
    with patch("src.services.email.smtp_sender._send_once") as mock_send:
        mock_send.side_effect = TimeoutError("to")
        with pytest.raises(TimeoutError):
            send_email_with_retry(msg_local, cfg=cfg_2)
    assert mock_send.call_count == 2


def test_retry_max_retries_1_sem_retry(cfg, msg):
    """Com max_retries=1, qualquer erro transitório falha sem retry."""
    cfg_1 = SmtpConfig(host="h", port=587, user="u", password="p", max_retries=1, retry_base=0.001)
    with patch("src.services.email.smtp_sender._send_once") as mock_send:
        mock_send.side_effect = TimeoutError("to")
        with pytest.raises(TimeoutError):
            send_email_with_retry(msg, cfg=cfg_1)
    assert mock_send.call_count == 1


# ── Timing do backoff ─────────────────────────────────────────────────────────

def test_retry_backoff_exponencial(cfg, msg):
    """Verifica que o tempo de espera cresce exponencialmente."""
    sleep_calls = []
    original_sleep = time.sleep

    with patch("src.services.email.smtp_sender.time") as mock_time:
        mock_time.sleep = lambda s: sleep_calls.append(s)
        with patch("src.services.email.smtp_sender._send_once") as mock_send:
            mock_send.side_effect = TimeoutError("to")
            with pytest.raises(TimeoutError):
                send_email_with_retry(msg, cfg=cfg)

    # Com retry_base=0.001 e 3 tentativas: espera após tentativa 1 e 2
    assert len(sleep_calls) == 2
    assert sleep_calls[0] == cfg.retry_base * (2 ** 0)  # 0.001s
    assert sleep_calls[1] == cfg.retry_base * (2 ** 1)  # 0.002s
    assert sleep_calls[1] > sleep_calls[0]              # cresce
