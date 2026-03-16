"""Testes unitários para src/auth/audit.py.

Executar com: pytest tests/test_audit.py -v
Não requer Supabase nem rede — o cliente de banco é mockado.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ── Stub mínimo de streamlit ──────────────────────────────────────────────────
if "streamlit" not in sys.modules:
    _st = types.ModuleType("streamlit")
    _st.cache_resource = lambda **kw: (lambda f: f)
    _st.session_state = {}
    sys.modules["streamlit"] = _st

import streamlit as st

import src.auth.audit as audit_mod


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_session_state():
    """Garante session_state limpo entre testes."""
    st.session_state.clear()
    yield
    st.session_state.clear()


@pytest.fixture()
def mock_svc():
    """Service client mockado que captura inserções."""
    svc = MagicMock()
    svc.table.return_value.insert.return_value.execute.return_value = None
    return svc


# ─────────────────────────────────────────────────────────────────────────────
# audit_log — comportamento básico
# ─────────────────────────────────────────────────────────────────────────────

def test_audit_log_insere_no_banco(mock_svc):
    with patch.object(audit_mod, "_get_svc", return_value=mock_svc):
        audit_mod.audit_log("test_event", tenant_id="t1", user_id="u1")

    mock_svc.table.assert_called_with("audit_logs")
    insert_call_args = mock_svc.table.return_value.insert.call_args[0][0]
    assert insert_call_args["event"] == "test_event"
    assert insert_call_args["tenant_id"] == "t1"
    assert insert_call_args["user_id"] == "u1"


def test_audit_log_resolve_session_state(mock_svc):
    st.session_state["current_tenant_id"] = "tenant-abc"
    st.session_state["sb_user_id"] = "user-xyz"

    with patch.object(audit_mod, "_get_svc", return_value=mock_svc):
        audit_mod.audit_log("any_event")

    payload = mock_svc.table.return_value.insert.call_args[0][0]
    assert payload["tenant_id"] == "tenant-abc"
    assert payload["user_id"] == "user-xyz"


def test_audit_log_nao_quebra_sem_banco():
    """Se _get_svc falhar, audit_log não deve levantar exceção."""
    with patch.object(audit_mod, "_get_svc", return_value=None):
        audit_mod.audit_log("no_db_event")  # não deve explodir


def test_audit_log_nao_quebra_com_excecao_no_banco(mock_svc):
    mock_svc.table.return_value.insert.return_value.execute.side_effect = RuntimeError("DB down")
    with patch.object(audit_mod, "_get_svc", return_value=mock_svc):
        audit_mod.audit_log("resilient_event")  # não deve propagar exceção


def test_audit_log_exclui_nones_do_payload(mock_svc):
    with patch.object(audit_mod, "_get_svc", return_value=mock_svc):
        audit_mod.audit_log("evt", tenant_id="t1", target_type=None)

    payload = mock_svc.table.return_value.insert.call_args[0][0]
    assert "target_type" not in payload  # None foi removido


def test_audit_log_com_metadata(mock_svc):
    with patch.object(audit_mod, "_get_svc", return_value=mock_svc):
        audit_mod.audit_log("evt", metadata={"foo": "bar", "n": 42})

    payload = mock_svc.table.return_value.insert.call_args[0][0]
    assert payload["metadata"] == {"foo": "bar", "n": 42}


def test_audit_log_metadata_vazio_por_padrao(mock_svc):
    with patch.object(audit_mod, "_get_svc", return_value=mock_svc):
        audit_mod.audit_log("evt", tenant_id="t1", user_id="u1")

    payload = mock_svc.table.return_value.insert.call_args[0][0]
    assert payload["metadata"] == {}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers semânticos
# ─────────────────────────────────────────────────────────────────────────────

def test_audit_login_success(mock_svc):
    with patch.object(audit_mod, "_get_svc", return_value=mock_svc):
        audit_mod.audit_login_success("joao@x.com", "uid-123")
    payload = mock_svc.table.return_value.insert.call_args[0][0]
    assert payload["event"] == "login_success"
    assert payload["actor_email"] == "joao@x.com"
    assert payload["user_id"] == "uid-123"


def test_audit_login_failure(mock_svc):
    with patch.object(audit_mod, "_get_svc", return_value=mock_svc):
        audit_mod.audit_login_failure("hacker@x.com")
    payload = mock_svc.table.return_value.insert.call_args[0][0]
    assert payload["event"] == "login_failure"
    assert payload["actor_email"] == "hacker@x.com"


def test_audit_login_blocked(mock_svc):
    with patch.object(audit_mod, "_get_svc", return_value=mock_svc):
        audit_mod.audit_login_blocked("hacker@x.com", 900)
    payload = mock_svc.table.return_value.insert.call_args[0][0]
    assert payload["event"] == "login_blocked"
    assert payload["metadata"]["blocked_for_seconds"] == 900


def test_audit_logout(mock_svc):
    with patch.object(audit_mod, "_get_svc", return_value=mock_svc):
        audit_mod.audit_logout("uid-456")
    payload = mock_svc.table.return_value.insert.call_args[0][0]
    assert payload["event"] == "logout"
    assert payload["user_id"] == "uid-456"


def test_audit_user_created(mock_svc):
    with patch.object(audit_mod, "_get_svc", return_value=mock_svc):
        audit_mod.audit_user_created("uid-new", "novo@x.com", "gestor")
    payload = mock_svc.table.return_value.insert.call_args[0][0]
    assert payload["event"] == "user_created"
    assert payload["target_type"] == "user"
    assert payload["target_id"] == "uid-new"
    assert payload["metadata"]["role"] == "gestor"


def test_audit_user_deleted(mock_svc):
    with patch.object(audit_mod, "_get_svc", return_value=mock_svc):
        audit_mod.audit_user_deleted("uid-del", "saiu@x.com")
    payload = mock_svc.table.return_value.insert.call_args[0][0]
    assert payload["event"] == "user_deleted"
    assert payload["target_id"] == "uid-del"


def test_audit_user_role_changed(mock_svc):
    with patch.object(audit_mod, "_get_svc", return_value=mock_svc):
        audit_mod.audit_user_role_changed("uid-r", "gestor", "admin")
    payload = mock_svc.table.return_value.insert.call_args[0][0]
    assert payload["event"] == "user_role_changed"
    assert payload["metadata"]["old_role"] == "gestor"
    assert payload["metadata"]["new_role"] == "admin"


def test_audit_equipment_deleted(mock_svc):
    with patch.object(audit_mod, "_get_svc", return_value=mock_svc):
        audit_mod.audit_equipment_deleted("eq-001", "TRT-1234")
    payload = mock_svc.table.return_value.insert.call_args[0][0]
    assert payload["event"] == "equipment_deleted"
    assert payload["target_type"] == "equipment"
    assert payload["metadata"]["frota"] == "TRT-1234"


def test_audit_equipment_moved(mock_svc):
    with patch.object(audit_mod, "_get_svc", return_value=mock_svc):
        audit_mod.audit_equipment_moved("eq-002", "grupo-A", "grupo-B")
    payload = mock_svc.table.return_value.insert.call_args[0][0]
    assert payload["event"] == "equipment_moved"
    assert payload["metadata"]["from_group"] == "grupo-A"
    assert payload["metadata"]["to_group"] == "grupo-B"


def test_audit_password_reset(mock_svc):
    with patch.object(audit_mod, "_get_svc", return_value=mock_svc):
        audit_mod.audit_password_reset("usuario@x.com")
    payload = mock_svc.table.return_value.insert.call_args[0][0]
    assert payload["event"] == "password_reset_requested"
    assert payload["actor_email"] == "usuario@x.com"
