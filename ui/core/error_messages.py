"""Mapeamento centralizado de erros para mensagens amigáveis ao usuário.

Substitui o padrão `st.error(f"Erro: {repr(e)}")` espalhado pelo código.

Uso:
    from src.ui.core.error_messages import friendly_error, show_error

    show_error(e)                     # exibe st.error com mensagem amigável
    msg = friendly_error(e)           # retorna string
"""
from __future__ import annotations

import streamlit as st


# Mapeamento de substrings de erro → mensagem amigável
_ERROR_MAP: list[tuple[str, str]] = [
    # Autenticação / Supabase
    ("Invalid login credentials",  "E-mail ou senha incorretos."),
    ("invalid_credentials",        "E-mail ou senha incorretos."),
    ("Email not confirmed",        "Confirme seu e-mail antes de entrar."),
    ("User not found",             "Nenhuma conta encontrada com este e-mail."),
    ("paused",                     "O projeto Supabase esta pausado. Reative em app.supabase.com."),
    ("inactive",                   "O projeto Supabase esta inativo. Verifique sua conta."),
    ("Too many requests",          "Muitas tentativas seguidas. Aguarde alguns minutos."),
    ("rate limit",                 "Limite de requisicoes atingido. Tente novamente em instantes."),
    ("JWT expired",                "Sua sessao expirou. Faca login novamente."),
    ("refresh_token_not_found",    "Sessao invalida. Faca login novamente."),
    # Rede / conectividade
    ("Failed to establish",        "Sem conexao com o servidor. Verifique sua internet."),
    ("Connection refused",         "Servico indisponivel. Tente novamente em instantes."),
    ("timeout",                    "A requisicao demorou demais. Tente novamente."),
    # Banco de dados
    ("unique constraint",          "Ja existe um registro com esse nome ou identificador."),
    ("duplicate key",              "Registro duplicado. Verifique se o item ja foi cadastrado."),
    ("foreign key",                "Operacao nao permitida: este registro esta vinculado a outros dados."),
    ("permission denied",          "Voce nao tem permissao para realizar esta acao."),
    ("RLS",                        "Acesso negado pela politica de seguranca do banco."),
    ("relation",                   "Tabela nao encontrada. Verifique se as migracoes foram aplicadas."),
    # Generico
    ("null value",                 "Um campo obrigatorio nao foi preenchido."),
    ("violates not-null",          "Preencha todos os campos obrigatorios antes de salvar."),
]

_FALLBACK = (
    "Ocorreu um erro inesperado. "
    "Se o problema persistir, copie o codigo de referencia e contate o suporte."
)


def friendly_error(exc: Exception) -> str:
    """Converte uma excecao em mensagem legivel para o usuario."""
    raw = str(exc).lower()
    for keyword, message in _ERROR_MAP:
        if keyword.lower() in raw:
            return message
    # Gera codigo curto reproducivel sem expor detalhes internos
    code = hex(abs(hash(str(exc))) % 0xFFFF)[2:].upper().zfill(4)
    return f"{_FALLBACK} (ref: {code})"


def show_error(exc: Exception, prefix: str = "") -> None:
    """Exibe st.error com mensagem amigavel. Inclui detalhes em expander para admins."""
    msg = friendly_error(exc)
    st.error(f"{prefix}{msg}" if prefix else msg)

    # Detalhes tecnicos apenas para admins (opt-in via session_state)
    role = str(st.session_state.get("current_role", "")).lower()
    if role in ("admin", "superadmin"):
        with st.expander("Detalhes tecnicos (visivel apenas para admins)", expanded=False):
            st.code(repr(exc), language="text")


def show_supabase_error(exc: Exception, context: str = "") -> None:
    """Variante para erros de operacoes no Supabase."""
    prefix = f"{context}: " if context else ""
    show_error(exc, prefix=prefix)
