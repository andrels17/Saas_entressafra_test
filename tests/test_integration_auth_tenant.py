"""Testes de integração: fluxo auth → tenant → query ao Supabase.

Estratégia: mock do Supabase client — não requer rede, mas exercita toda
a lógica de negócio real (session_state, role validation, scope filtering,
repository query building).

Camadas testadas:
  1. session_state ↔ auth helpers  (set_auth_session / is_logged_in / hard_logout)
  2. Role resolution               (refresh_current_role via mock Supabase)
  3. Scope enforcement             (get_my_scope + can_view_all_data por role)
  4. Repository query building     (safe_select com filtros tenant_id + role)
  5. Ponta-a-ponta fictícia        (fluxo login → tenant select → query)

Execução:
    pytest tests/test_integration_auth_tenant.py -v
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ── Stub completo de streamlit ───────────────────────────────────────────────

def _make_streamlit_stub():
    _st = types.ModuleType("streamlit")

    # session_state como dict real
    _st.session_state = {}

    # cache decorators: passthrough (retorna a função sem alteração)
    _st.cache_resource = lambda **kw: (lambda f: f)
    _st.cache_data     = lambda **kw: (lambda f: f)

    # Atributos .clear() exigidos por _clear_all_caches()
    _st.cache_data.clear     = lambda: None
    _st.cache_resource.clear = lambda: None

    # secrets como dict (evita AttributeError no rate_limit._get_backend)
    _st.secrets = {}

    # Widgets e funções de UI (não usados em testes, mas precisam existir)
    for name in ("stop", "error", "warning", "info", "success",
                 "rerun", "markdown", "write", "caption"):
        setattr(_st, name, lambda *a, **k: None)

    return _st


if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = _make_streamlit_stub()
else:
    # Já importado (ex: rodando junto com outros testes) — garantir atributos
    import streamlit as _st_existing
    if not hasattr(_st_existing, "secrets"):
        _st_existing.secrets = {}
    if not hasattr(_st_existing.cache_data, "clear"):
        _st_existing.cache_data.clear = lambda: None
    if not hasattr(_st_existing.cache_resource, "clear"):
        _st_existing.cache_resource.clear = lambda: None

import streamlit as st

# Stub mínimo do módulo supabase (evita ImportError em repositories/base.py)
if "supabase" not in sys.modules:
    _supabase_mod = types.ModuleType("supabase")
    _supabase_mod.Client = object
    _supabase_mod.create_client = lambda url, key: None
    sys.modules["supabase"] = _supabase_mod

# ── Imports do sistema após stubs ────────────────────────────────────────────

sys.path.insert(0, ".")

from src.auth.session import (
    set_auth_session, is_logged_in, clear_auth_session,
    clear_derived_state, hard_logout,
)
from src.auth.roles import Role
from src.auth.guard import require_login, require_role, is_admin, is_manager
from src.auth.permissions import can_view_all_data
import src.auth.rate_limit as rl


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_session():
    """Garante session_state limpo antes de cada teste."""
    st.session_state.clear()
    yield
    st.session_state.clear()


@pytest.fixture(autouse=True)
def isolate_rate_limit():
    """Injeta store em memória isolado para cada teste."""
    fresh: dict = {}
    original = rl._get_memory_store
    rl._get_memory_store = lambda: fresh   # type: ignore[attr-defined]
    yield fresh
    rl._get_memory_store = original        # type: ignore[attr-defined]


def _login(role: str = "user", tenant_id: str = "t1", user_id: str = "u1") -> None:
    """Simula login completo no session_state."""
    set_auth_session(access_token="tok_abc", refresh_token="ref_xyz", user_id=user_id)
    st.session_state["current_tenant_id"] = tenant_id
    st.session_state["current_role"]      = role


# ════════════════════════════════════════════════════════════════════════════
# 1. Auth session helpers
# ════════════════════════════════════════════════════════════════════════════

class TestAuthSession:
    def test_set_auth_session_popula_state(self):
        set_auth_session("tok", "ref", "uid_1")
        assert st.session_state["sb_access_token"]  == "tok"
        assert st.session_state["sb_refresh_token"] == "ref"
        assert st.session_state["sb_user_id"]       == "uid_1"

    def test_is_logged_in_false_sem_token(self):
        assert is_logged_in() is False

    def test_is_logged_in_true_com_token(self):
        set_auth_session("tok", "ref", "uid")
        assert is_logged_in() is True

    def test_clear_auth_session_remove_tokens(self):
        set_auth_session("tok", "ref", "uid")
        clear_auth_session()
        assert not st.session_state.get("sb_access_token")
        assert is_logged_in() is False

    def test_hard_logout_limpa_tokens_e_role(self):
        _login(role="admin")
        hard_logout()
        assert not st.session_state.get("sb_access_token")
        assert not st.session_state.get("current_role")

    def test_hard_logout_limpa_estado_de_ui(self):
        _login(role="admin")
        st.session_state["some_ui_filter"] = "valor"
        hard_logout()
        assert not st.session_state.get("some_ui_filter")

    def test_hard_logout_preserva_chaves_streamlit_internas(self):
        _login(role="admin")
        st.session_state["__streamlit_internal"] = "keep"
        hard_logout()
        assert st.session_state.get("__streamlit_internal") == "keep"

    def test_hard_logout_deixa_session_utilizavel(self):
        _login(role="admin")
        hard_logout()
        # Deve ser possível fazer login novamente após logout
        set_auth_session("new_tok", "new_ref", "uid2")
        assert is_logged_in() is True

    def test_clear_derived_state_mantem_tokens(self):
        set_auth_session("tok", "ref", "uid")
        st.session_state["current_role"]      = "admin"
        st.session_state["current_tenant_id"] = "t1"
        clear_derived_state()
        # tokens permanecem
        assert st.session_state.get("sb_access_token") == "tok"
        # estado derivado foi limpo
        assert not st.session_state.get("current_role")
        assert not st.session_state.get("current_tenant_id")

    def test_multiplos_logins_nao_vazam_estado(self):
        """Logout + novo login não deve herdar estado do usuário anterior."""
        _login(role="admin", tenant_id="t_antigo", user_id="u_antigo")
        hard_logout()
        _login(role="user", tenant_id="t_novo", user_id="u_novo")
        assert st.session_state["current_role"]      == "user"
        assert st.session_state["current_tenant_id"] == "t_novo"
        assert st.session_state["sb_user_id"]        == "u_novo"


# ════════════════════════════════════════════════════════════════════════════
# 2. Role model
# ════════════════════════════════════════════════════════════════════════════

class TestRoleModel:
    def test_role_enum_values(self):
        assert Role.USER       == "user"
        assert Role.GESTOR     == "gestor"
        assert Role.SUPERVISOR == "supervisor"
        assert Role.ADMIN      == "admin"
        assert Role.SUPERADMIN == "superadmin"

    @pytest.mark.parametrize("role", ["admin", "superadmin"])
    def test_is_admin_true(self, role):
        assert Role.is_admin(role) is True

    @pytest.mark.parametrize("role", ["user", "gestor", "supervisor", "", None])
    def test_is_admin_false(self, role):
        assert Role.is_admin(role) is False

    @pytest.mark.parametrize("role", ["gestor", "supervisor", "admin", "superadmin"])
    def test_is_manager_true(self, role):
        assert Role.is_manager(role) is True

    @pytest.mark.parametrize("role", ["user", "", None])
    def test_is_manager_false(self, role):
        assert Role.is_manager(role) is False

    def test_admin_roles_frozenset_conteudo(self):
        assert "admin"      in Role.ADMIN_ROLES
        assert "superadmin" in Role.ADMIN_ROLES
        assert "gestor"     not in Role.ADMIN_ROLES
        assert "user"       not in Role.ADMIN_ROLES

    def test_manager_roles_inclui_gestor(self):
        assert "gestor" in Role.MANAGER_ROLES

    def test_supervisor_roles_inclui_supervisor(self):
        assert "supervisor" in Role.SUPERVISOR_ROLES

    def test_all_roles_contem_todos(self):
        for r in ["user", "gestor", "supervisor", "admin", "superadmin"]:
            assert r in Role.ALL_ROLES

    @pytest.mark.parametrize("value,expected", [
        ("admin",  Role.ADMIN),
        ("user",   Role.USER),
        ("gestor", Role.GESTOR),
    ])
    def test_from_str_valido(self, value, expected):
        assert Role.from_str(value) == expected

    @pytest.mark.parametrize("value", ["root", "superuser", "", None])
    def test_from_str_invalido_retorna_none(self, value):
        assert Role.from_str(value) is None

    def test_role_e_string(self):
        """Role deve comparar igual a string para compatibilidade."""
        assert Role.ADMIN == "admin"
        assert "admin" == Role.ADMIN


# ════════════════════════════════════════════════════════════════════════════
# 3. Guards de autorização
# ════════════════════════════════════════════════════════════════════════════

class TestGuards:
    def test_require_login_stop_sem_token(self):
        with patch("streamlit.stop", side_effect=SystemExit("stop")):
            with pytest.raises(SystemExit):
                require_login()

    def test_require_login_passa_com_token(self):
        set_auth_session("tok", "ref", "uid")
        require_login()  # não deve levantar exceção

    def test_require_role_bloqueia_role_errado(self):
        _login(role="user")
        with patch("streamlit.stop", side_effect=SystemExit("stop")):
            with pytest.raises(SystemExit):
                require_role("admin")

    def test_require_role_passa_role_correto(self):
        _login(role="admin")
        require_role("admin")

    def test_require_role_aceita_multiplos_roles(self):
        _login(role="gestor")
        require_role("admin", "gestor", "supervisor")

    def test_require_role_lista_vazia_passa_sempre(self):
        _login(role="user")
        require_role()  # sem roles exigidos → sempre passa

    def test_is_admin_true_na_sessao(self):
        _login(role="admin")
        assert is_admin() is True

    def test_is_admin_false_para_user(self):
        _login(role="user")
        assert is_admin() is False

    def test_is_admin_false_sem_login(self):
        assert is_admin() is False

    def test_is_manager_true_gestor(self):
        _login(role="gestor")
        assert is_manager() is True

    def test_is_manager_false_user(self):
        _login(role="user")
        assert is_manager() is False


# ════════════════════════════════════════════════════════════════════════════
# 4. Permissões
# ════════════════════════════════════════════════════════════════════════════

class TestPermissions:
    @pytest.mark.parametrize("role", ["admin", "superadmin", "supervisor"])
    def test_pode_ver_tudo_roles_elevados(self, role):
        assert can_view_all_data(role) is True

    @pytest.mark.parametrize("role", ["gestor", "user", "", None])
    def test_nao_pode_ver_tudo_roles_restritos(self, role):
        assert can_view_all_data(role) is False


# ════════════════════════════════════════════════════════════════════════════
# 5. Repository — montagem de queries
# ════════════════════════════════════════════════════════════════════════════

class TestRepositoryQueryBuilding:

    def _make_mock_sb(self, return_data: list):
        mock_result = MagicMock()
        mock_result.data = return_data

        mock_q = MagicMock()
        for method in ("select", "eq", "neq", "in_", "gte", "lte", "order", "limit"):
            getattr(mock_q, method).return_value = mock_q
        mock_q.execute.return_value = mock_result

        mock_sb = MagicMock()
        mock_sb.table.return_value = mock_q
        return mock_sb, mock_q

    def test_safe_select_retorna_dados(self):
        from src.repositories.base import safe_select
        sb, q = self._make_mock_sb([{"id": "1"}])
        result = safe_select(sb, "equipamentos", "id,nome")
        assert result == [{"id": "1"}]
        q.select.assert_called_once_with("id,nome")

    def test_safe_select_aplica_filtro_eq(self):
        from src.repositories.base import safe_select
        sb, q = self._make_mock_sb([])
        safe_select(sb, "tarefas", "*", tenant_id__eq="t1")
        calls = {c.args for c in q.eq.call_args_list}
        assert ("tenant_id", "t1") in calls

    def test_safe_select_aplica_filtro_in(self):
        from src.repositories.base import safe_select
        sb, q = self._make_mock_sb([])
        safe_select(sb, "equipamentos", "*", grupo_id__in=["g1", "g2"])
        q.in_.assert_any_call("grupo_id", ["g1", "g2"])

    def test_safe_select_aplica_filtro_neq(self):
        from src.repositories.base import safe_select
        sb, q = self._make_mock_sb([])
        safe_select(sb, "tarefas", "*", status__neq="concluido")
        q.neq.assert_any_call("status", "concluido")

    def test_safe_select_ignora_filtro_none(self):
        from src.repositories.base import safe_select
        sb, q = self._make_mock_sb([])
        safe_select(sb, "equipamentos", "*", campo__eq=None)
        for call in q.eq.call_args_list:
            assert None not in call.args

    def test_safe_select_retorna_lista_vazia_em_excecao(self):
        from src.repositories.base import safe_select
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("Supabase offline")
        result = safe_select(sb, "qualquer", "*")
        assert result == []

    def test_safe_select_filtro_atalho_sem_sufixo(self):
        """Filtro sem sufixo (__eq) deve ser tratado como .eq()."""
        from src.repositories.base import safe_select
        sb, q = self._make_mock_sb([])
        safe_select(sb, "tarefas", "*", revisao_id="rev123")
        q.eq.assert_any_call("revisao_id", "rev123")

    def test_safe_select_multiplos_filtros(self):
        from src.repositories.base import safe_select
        sb, q = self._make_mock_sb([])
        safe_select(sb, "tarefas", "*",
                    tenant_id__eq="t1",
                    revisao_id__eq="r1",
                    status__neq="concluido")
        eq_calls = {c.args for c in q.eq.call_args_list}
        assert ("tenant_id", "t1") in eq_calls
        assert ("revisao_id", "r1") in eq_calls

    def test_safe_select_in_lista_vazia_nao_aplica_filtro(self):
        """in_ com lista vazia não deve chamar .in_() para evitar query inválida."""
        from src.repositories.base import safe_select
        sb, q = self._make_mock_sb([])
        safe_select(sb, "equipamentos", "*", grupo_id__in=[])
        q.in_.assert_not_called()


# ════════════════════════════════════════════════════════════════════════════
# 6. Fluxo ponta-a-ponta: login → tenant → query com scope
# ════════════════════════════════════════════════════════════════════════════

class TestEndToEndAuthTenantQuery:

    def _mock_supabase_role(self, role: str):
        mock_res = MagicMock()
        mock_res.data = [{"role": role}]
        mock_q = MagicMock()
        for m in ("select", "eq", "limit"):
            getattr(mock_q, m).return_value = mock_q
        mock_q.execute.return_value = mock_res
        mock_sb = MagicMock()
        mock_sb.table.return_value = mock_q
        mock_sb.postgrest = MagicMock()
        return mock_sb

    def test_estado_completo_apos_login(self):
        _login(role="gestor", tenant_id="t_abc", user_id="u_abc")
        assert is_logged_in()
        assert st.session_state["current_tenant_id"] == "t_abc"
        assert st.session_state["current_role"]      == "gestor"
        assert st.session_state["sb_user_id"]        == "u_abc"

    def test_logout_apaga_tenant_e_role(self):
        _login(role="admin", tenant_id="t_xyz")
        hard_logout()
        assert not st.session_state.get("current_tenant_id")
        assert not st.session_state.get("current_role")
        assert is_logged_in() is False

    def test_admin_sem_restricao_scope(self):
        _login(role="admin")
        assert can_view_all_data("admin") is True

    def test_gestor_com_restricao_scope(self):
        _login(role="gestor")
        assert can_view_all_data("gestor") is False

    def test_role_invalido_sem_permissao(self):
        _login(role="desconhecido")
        assert can_view_all_data("desconhecido") is False
        assert is_admin() is False
        assert is_manager() is False

    def test_refresh_current_role_via_mock_supabase(self):
        """Valida que refresh_current_role lê o role do banco e atualiza session_state."""
        _login(role="user", tenant_id="t1", user_id="u1")
        mock_sb = self._mock_supabase_role("admin")

        with patch("src.auth.tenant.get_supabase_anon", return_value=mock_sb):
            from src.auth.tenant import refresh_current_role
            role = refresh_current_role()

        assert role == "admin"
        assert st.session_state.get("current_role") == "admin"

    def test_refresh_current_role_sem_token_retorna_vazio(self):
        """Sem token na sessão não deve crashar."""
        from src.auth.tenant import refresh_current_role
        role = refresh_current_role()
        assert role == ""

    def test_query_aplica_tenant_scope(self):
        """Verifica que a query ao repositório aplica tenant_id."""
        from src.repositories.base import safe_select
        mock_result = MagicMock()
        mock_result.data = [{"id": "eq1", "frota": "F01"}]
        mock_q = MagicMock()
        for m in ("select", "eq"):
            getattr(mock_q, m).return_value = mock_q
        mock_q.execute.return_value = mock_result
        mock_sb = MagicMock()
        mock_sb.table.return_value = mock_q

        _login(role="admin", tenant_id="t1")
        result = safe_select(mock_sb, "equipamentos", "id,frota",
                             tenant_id__eq="t1", ativo__eq=True)

        calls = {c.args for c in mock_q.eq.call_args_list}
        assert ("tenant_id", "t1") in calls
        assert result == [{"id": "eq1", "frota": "F01"}]

    def test_superadmin_tem_acesso_total(self):
        _login(role="superadmin")
        assert can_view_all_data("superadmin") is True
        assert is_admin() is True
        assert is_manager() is True


# ════════════════════════════════════════════════════════════════════════════
# 7. Rate limiting no contexto de login
# ════════════════════════════════════════════════════════════════════════════

class TestRateLimitIntegration:
    """Testa rate limiting usando o backend _MemoryBackend direto
    (sem passar por _get_backend para evitar dependência de st.secrets)."""

    def setup_method(self):
        self.store: dict = {}
        # Injeta store isolado no backend de memória
        rl._get_memory_store = lambda: self.store  # type: ignore[attr-defined]
        self.backend = rl._MemoryBackend.__new__(rl._MemoryBackend)

    def test_chave_nova_e_permitida(self):
        key = rl.get_rate_limit_key("novo@test.com")
        allowed, msg, wait = self.backend.check(key)
        assert allowed is True
        assert wait == 0
        assert msg == ""

    def test_primeira_falha_decrementa_tentativas(self):
        key = rl.get_rate_limit_key("user@test.com")
        remaining = self.backend.record_failure(key)
        assert remaining == rl.MAX_ATTEMPTS - 1

    def test_falhas_acumulam_corretamente(self):
        key = rl.get_rate_limit_key("acum@test.com")
        for _ in range(3):
            self.backend.record_failure(key)
        remaining = self.backend.record_failure(key)
        assert remaining == rl.MAX_ATTEMPTS - 4

    def test_bloqueio_apos_max_tentativas(self):
        key = rl.get_rate_limit_key("brute@test.com")
        for _ in range(rl.MAX_ATTEMPTS):
            self.backend.record_failure(key)
        allowed, msg, wait = self.backend.check(key)
        assert allowed is False
        assert wait > 0
        assert "bloqueado" in msg.lower() or "bloqueada" in msg.lower()

    def test_tentativa_apos_bloqueio_retorna_zero(self):
        key = rl.get_rate_limit_key("zero@test.com")
        for _ in range(rl.MAX_ATTEMPTS):
            self.backend.record_failure(key)
        remaining = self.backend.record_failure(key)
        assert remaining == 0

    def test_sucesso_limpa_bucket(self):
        key = rl.get_rate_limit_key("clean@test.com")
        self.backend.record_failure(key)
        self.backend.record_failure(key)
        self.backend.record_success(key)
        assert key not in self.store

    def test_apos_sucesso_permite_novamente(self):
        key = rl.get_rate_limit_key("retry@test.com")
        self.backend.record_failure(key)
        self.backend.record_success(key)
        allowed, _, _ = self.backend.check(key)
        assert allowed is True

    def test_sucesso_em_chave_inexistente_nao_quebra(self):
        self.backend.record_success("login:naoexiste@test.com")

    def test_chave_normaliza_email_maiusculo(self):
        k1 = rl.get_rate_limit_key("ADMIN@EMPRESA.COM")
        k2 = rl.get_rate_limit_key("admin@empresa.com")
        assert k1 == k2

    def test_chave_strip_espacos(self):
        k1 = rl.get_rate_limit_key("  joao@x.com  ")
        k2 = rl.get_rate_limit_key("joao@x.com")
        assert k1 == k2

    def test_bloqueio_nao_afeta_outro_usuario(self):
        key_a = rl.get_rate_limit_key("userA@test.com")
        key_b = rl.get_rate_limit_key("userB@test.com")
        for _ in range(rl.MAX_ATTEMPTS):
            self.backend.record_failure(key_a)
        allowed_b, _, _ = self.backend.check(key_b)
        assert allowed_b is True

    def test_get_attempts_info_chave_nova(self):
        key = rl.get_rate_limit_key("info@test.com")
        info = self.backend.get_info(key)
        assert info["attempts_in_window"] == 0
        assert info["locked"] is False
        assert info["locked_until"] is None

    def test_get_attempts_info_apos_duas_falhas(self):
        key = rl.get_rate_limit_key("info2@test.com")
        self.backend.record_failure(key)
        self.backend.record_failure(key)
        info = self.backend.get_info(key)
        assert info["attempts_in_window"] == 2
        assert info["locked"] is False

    def test_get_attempts_info_bloqueado(self):
        key = rl.get_rate_limit_key("infoblk@test.com")
        for _ in range(rl.MAX_ATTEMPTS):
            self.backend.record_failure(key)
        info = self.backend.get_info(key)
        assert info["locked"] is True
        assert info["locked_until"] is not None

    def test_tentativas_expiradas_nao_contam(self):
        """Falhas fora da janela de WINDOW_SECONDS são ignoradas."""
        import time
        key = rl.get_rate_limit_key("expire@test.com")
        bucket = self.backend._bucket(key)
        old_ts = time.time() - rl.WINDOW_SECONDS - 10
        bucket.attempts = [old_ts] * (rl.MAX_ATTEMPTS - 1)
        allowed, _, _ = self.backend.check(key)
        assert allowed is True

    def test_lockout_ja_expirado_libera_acesso(self):
        """Após LOCKOUT_SECONDS, o bloqueio deve ser liberado."""
        import time
        key = rl.get_rate_limit_key("unlock@test.com")
        bucket = self.backend._bucket(key)
        bucket.locked_until = time.time() - 1  # já expirou
        allowed, _, _ = self.backend.check(key)
        assert allowed is True
