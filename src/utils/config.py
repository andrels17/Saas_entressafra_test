"""Validação de configuração — verifica secrets no startup do app.

Detecta configurações ausentes ou inválidas antes que causem erros
obscuros em runtime. Exibe mensagem clara e para a aplicação.

Uso no app.py (antes de qualquer outra lógica):
    from src.utils.config import validate_config_or_stop
    validate_config_or_stop()

Secrets obrigatórias:
    SUPABASE_URL             URL do projeto Supabase
    SUPABASE_ANON_KEY        Chave anon para o cliente frontend
    SUPABASE_SERVICE_ROLE_KEY Chave de serviço para operações admin

Secrets opcionais mas recomendadas:
    SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD  (e-mail)
    SENTRY_DSN                                          (monitoramento)
    APP_NAME                                            (nome do app na UI)
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("saas.config")

# ── Definição das secrets ───────────────────────────────────────────────

_REQUIRED: list[tuple[str, str]] = [
    ("SUPABASE_URL", "URL do projeto Supabase (ex: https://xyz.supabase.co)"),
    ("SUPABASE_ANON_KEY", "Chave anon do Supabase (Settings → API → anon key)"),
    ("SUPABASE_SERVICE_ROLE_KEY", "Chave service_role do Supabase (Settings → API → service_role)"),
]

_RECOMMENDED: list[tuple[str, str]] = [
    ("SMTP_HOST", "Servidor SMTP para envio de e-mails"),
    ("SMTP_PORT", "Porta SMTP (ex: 587)"),
    ("SMTP_USER", "Usuário SMTP"),
    ("SMTP_PASSWORD", "Senha SMTP"),
]

# Padrões de validação (simples — não exaustivos)
_VALIDATORS: dict[str, tuple[str, str]] = {
    "SUPABASE_URL": (
        r"^https://[a-z0-9]+\.supabase\.co$",
        "Deve ser https://<projeto>.supabase.co",
    ),
    "SMTP_PORT": (
        r"^\d{2,5}$",
        "Deve ser um número de porta (ex: 587, 465)",
    ),
}


# ── Resultado da validação ──────────────────────────────────────────────

class ConfigError(Exception):
    """Lançada quando uma secret obrigatória está ausente ou inválida."""


def _get_secrets():
    """Retorna st.secrets ou fallback de variáveis de ambiente."""
    try:
        import streamlit as st
        return st.secrets
    except Exception:
        import os

        class _Env:
            def get(self, k, d=None): return os.environ.get(k, d)
            def __getitem__(self, k): return os.environ[k]
        return _Env()


def validate_config() -> tuple[list[str], list[str]]:
    """Valida a configuração e retorna (erros, avisos).

    Returns:
        errors:   Lista de problemas críticos (secrets obrigatórias ausentes/inválidas).
        warnings: Lista de problemas não-críticos (secrets recomendadas ausentes).
    """
    secrets = _get_secrets()
    errors: list[str] = []
    warnings: list[str] = []

    # Obrigatórias
    for key, desc in _REQUIRED:
        value = secrets.get(key, "")
        if not value:
            errors.append(f"❌ **{key}** ausente — {desc}")
            continue
        # Valida formato se houver regra
        if key in _VALIDATORS:
            pattern, hint = _VALIDATORS[key]
            if not re.match(pattern, str(value).strip()):
                errors.append(f"❌ **{key}** formato inválido — {hint}")

    # Recomendadas (apenas aviso)
    smtp_keys = [k for k, _ in _RECOMMENDED]
    smtp_present = [k for k in smtp_keys if secrets.get(k)]
    smtp_missing = [k for k in smtp_keys if not secrets.get(k)]

    if smtp_missing and smtp_present:
        # Algumas chaves SMTP presentes mas não todas
        warnings.append(
            f"⚠️ Configuração SMTP incompleta — ausentes: {', '.join(smtp_missing)}. "
            "O envio de relatórios por e-mail não funcionará."
        )
    elif smtp_missing and not smtp_present:
        warnings.append(
            "⚠️ Configuração SMTP não encontrada. "
            "Adicione SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD para habilitar e-mails.")

    # Log resumo
    if errors:
        log.error(
            "Configuração inválida: %d erro(s), %d aviso(s)",
            len(errors),
            len(warnings))
    elif warnings:
        log.warning("Configuração com avisos: %d aviso(s)", len(warnings))
    else:
        log.info("Configuração validada com sucesso.")

    return errors, warnings


def validate_config_or_stop() -> None:
    """Valida a configuração. Se houver erros críticos, exibe mensagem e para o app.

    Deve ser chamado no início do app.py, antes de qualquer lógica de negócio.
    Avisos são exibidos mas não param a aplicação.
    """
    import streamlit as st

    errors, warnings = validate_config()

    # Avisos: exibe uma vez por sessão
    if warnings and not st.session_state.get("_config_warnings_shown"):
        for w in warnings:
            st.warning(w)
        st.session_state["_config_warnings_shown"] = True

    # Erros críticos: para a aplicação com mensagem clara
    if errors:
        st.error("### ⚠️ Configuração incompleta")
        st.markdown(
            "O sistema não pode iniciar porque as seguintes configurações estão ausentes "
            "ou inválidas. Adicione-as ao arquivo `.streamlit/secrets.toml` ou nas "
            "variáveis de ambiente do servidor.")
        for err in errors:
            st.markdown(err)
        st.markdown(
            "---\n"
            "📖 Consulte a documentação de configuração no `README.md` do projeto.\n\n"
            "Após corrigir, reinicie o aplicativo.")
        st.stop()
