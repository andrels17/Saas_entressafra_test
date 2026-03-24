"""Gerenciamento de sessão de autenticação (Supabase + Streamlit session_state).

Responsabilidades:
  - Armazenar / limpar tokens de acesso no session_state
  - Verificar validade do token e renovar via refresh_token
  - Logout seguro (limpa caches para evitar vazamento de permissões)
"""
from __future__ import annotations

import logging
import time

import streamlit as st

# ── Chaves de sessão ────────────────────────────────────────────────────
_AUTH_KEYS = (
    "sb_access_token",
    "sb_refresh_token",
    "sb_user_id",
    "current_tenant_id",
    "current_role",
    "__nav_to")
_DERIVED_KEYS = (
    "current_tenant_id", "current_role", "__nav_to", "__current_page",
    "menu", "pages", "menu_pages", "_tenant_user_id",
    "scope_departamento_ids", "scope_grupo_ids",
    "__scope_departamento_ids", "__scope_grupo_ids", "_scope_signature",
)


# ── Escrita / leitura ───────────────────────────────────────────────────

def set_auth_session(
        access_token: str,
        refresh_token: str,
        user_id: str) -> None:
    """Persiste tokens e user_id no session_state."""
    st.session_state["sb_access_token"] = access_token
    st.session_state["sb_refresh_token"] = refresh_token
    st.session_state["sb_user_id"] = user_id


def is_logged_in() -> bool:
    """Retorna True se há um access_token armazenado."""
    return bool(st.session_state.get("sb_access_token"))


# ── Limpeza de estado ───────────────────────────────────────────────────

def clear_auth_session() -> None:
    """Remove tokens de autenticação. Use hard_logout() quando possível."""
    for k in _AUTH_KEYS:
        st.session_state.pop(k, None)


def clear_derived_state() -> None:
    """Limpa estado derivado (tenant/role/nav) sem derrubar tokens.

    Útil quando o Streamlit Cloud reutiliza a sessão do servidor entre usuários.
    """
    for k in _DERIVED_KEYS:
        st.session_state.pop(k, None)


def hard_logout() -> None:
    """Logout completo: limpa session_state sem derrubar caches de outros usuários.

    Streamlit mantém a mesma sessão do servidor após F5; então só limpar tokens
    não basta quando há estado derivado (escopo/permissão).

    Diferente da versão anterior, NÃO chama st.cache_data.clear() /
    st.cache_resource.clear() globalmente — isso invalidaria o cache de todos
    os usuários simultâneos no Streamlit Cloud. Em vez disso, invalida apenas
    as entradas do cache anon específicas deste usuário (via _anon_cache) e
    confia que os caches @st.cache_data são chaveados por (tenant_id, token),
    portanto expiram naturalmente quando o token muda.
    """
    # Remove o cliente anon cacheado para este token antes de limpar o estado
    try:
        from src.db.supabase_client import _anon_cache
        token = st.session_state.get("sb_access_token") or ""
        if token:
            # Remove todas as entradas associadas a este token
            keys_to_remove = [k for k in list(_anon_cache.keys()) if k[1] == token]
            for k in keys_to_remove:
                _anon_cache.pop(k, None)
    except Exception:
        pass

    # Limpa TODAS as chaves da sessão atual — incluindo com prefixo _ (estado de UI
    # de abas, filtros e scope) que não devem vazar entre sessões/usuários.
    # Exceção: chaves internas do Streamlit que começam com "__streamlit" são
    # ignoradas para não quebrar widgets em andamento.
    for k in list(st.session_state.keys()):
        if not k.startswith("__streamlit"):
            st.session_state.pop(k, None)

    try:
        st.query_params.clear()
    except Exception:
        pass  # ignorado intencionalmente — query_params pode não estar disponível

    st.rerun()


def reset_for_login_attempt() -> None:
    """Zera caches e estado de auth antes de uma nova tentativa de login."""
    clear_auth_session()
    _clear_all_caches()


def _clear_all_caches() -> None:
    for fn in (st.cache_data.clear, st.cache_resource.clear):
        try:
            fn()
        except Exception:
            pass  # ignorado — operação opcional


# ── Renovação de token ──────────────────────────────────────────────────

def try_refresh_session() -> bool:
    """Tenta renovar o access_token usando o refresh_token armazenado."""
    refresh_token = st.session_state.get("sb_refresh_token")
    if not refresh_token:
        return False

    try:
        from src.db.supabase_client import get_supabase_anon

        sb = get_supabase_anon()
        res = sb.auth.refresh_session(refresh_token)
        if res and res.session:
            set_auth_session(
                res.session.access_token,
                res.session.refresh_token,
                res.user.id if res.user else st.session_state.get(
                    "sb_user_id",
                    ""),
            )
            return True
    except Exception:
        pass  # ignorado — operação opcional
    return False


def ensure_valid_token() -> bool:
    """Garante que o access_token seja válido, renovando se necessário.

    Otimização: verifica o campo `exp` do JWT localmente antes de fazer
    qualquer chamada HTTP. Só aciona o banco quando o token está perto de
    expirar (< _LOCAL_VERIFY_BUFFER segundos) ou quando não foi verificado
    recentemente (> _REMOTE_VERIFY_INTERVAL segundos).

    Returns:
        True se o token é válido (ou foi renovado com sucesso), False caso contrário.
    """
    access_token = st.session_state.get("sb_access_token")
    if not access_token:
        return False

    now = time.time()
    _LOCAL_VERIFY_BUFFER = 120      # verifica remotamente se expira em < 2 min
    _REMOTE_VERIFY_INTERVAL = 300   # re-verifica no banco no máximo a cada 5 min

    # ── Verificação local do JWT (sem round-trip) ────────────────────────────
    try:
        import base64 as _b64
        import json as _json
        part = str(access_token).split(".")[1]
        part += "=" * (-len(part) % 4)
        payload = _json.loads(_b64.b64decode(part))
        exp = payload.get("exp", 0)
        time_left = exp - now

        if time_left < 0:
            # Token já expirado — tenta refresh imediatamente
            return try_refresh_session() or (clear_auth_session() or False)  # type: ignore[func-returns-value]

        if time_left > _LOCAL_VERIFY_BUFFER:
            # Token claramente válido: só vai ao banco se o intervalo remoto expirou
            last_remote = st.session_state.get("_token_last_remote_verify", 0)
            if now - float(last_remote) < _REMOTE_VERIFY_INTERVAL:
                # Dentro do intervalo seguro — confia no JWT local
                return True
    except Exception:
        # Falha ao decodificar JWT (malformado): segue para verificação remota
        pass

    # ── Verificação remota (chamada HTTP ao Supabase) ────────────────────────
    try:
        from src.db.supabase_client import get_supabase_anon

        sb = get_supabase_anon()
        sb.auth.set_session(
            access_token, st.session_state.get("sb_refresh_token", ""))
        user = sb.auth.get_user(access_token)

        if user and user.user:
            new_uid = getattr(user.user, "id", None) or ""
            prev_uid = st.session_state.get("sb_user_id") or ""
            st.session_state["sb_user_id"] = new_uid
            st.session_state["_token_last_remote_verify"] = now
            if new_uid and new_uid != prev_uid:
                clear_derived_state()
            return True

    except Exception as e:
        msg = str(e).lower()
        # Erros de autenticação explícitos → tentar refresh ou logout
        if any(k in msg for k in ("invalid", "expired", "jwt", "401", "403")):
            return try_refresh_session() or (
                clear_auth_session() or False)  # type: ignore[func-returns-value]

        # Erros transitórios de rede (timeout, 500, conexão recusada):
        # mantém a sessão, mas registra e rastreia a duração da falha.
        # Se o banco ficar indisponível por mais de 5 minutos consecutivos,
        # força logout para não deixar sessões em estado indefinido.
        _log = logging.getLogger("saas.session")
        _log.warning("ensure_valid_token: erro transitório: %s", e)

        _TRANSIENT_ERROR_KEY = "_token_transient_error_since"
        if "_token_transient_error_since" not in st.session_state:
            st.session_state[_TRANSIENT_ERROR_KEY] = now
        elif now - st.session_state[_TRANSIENT_ERROR_KEY] > 300:
            _log.error(
                "ensure_valid_token: banco indisponível por >5 min. "
                "Forçando logout."
            )
            clear_auth_session()
            return False

        return True  # erro transitório recente, mantém sessão

    # Chegou aqui sem retornar True dentro do try → token sem user válido
    st.session_state.pop("_token_transient_error_since", None)
    return False
