"""Audit log de ações administrativas e de segurança.

Registra eventos sensíveis na tabela `audit_logs` do Supabase usando
service role (para garantir escrita mesmo quando RLS bloqueia o usuário).

Eventos auditados:
  - login_success / login_failure / login_blocked
  - logout
  - user_created / user_deleted / user_role_changed
  - tenant_created
  - equipment_deleted / equipment_moved
  - password_reset_requested
  - departamento_criado / departamento_atualizado / departamento_deletado
  - departamento_ativado / departamento_desativado
  - grupo_criado / grupo_atualizado / grupo_deletado
  - grupo_ativado / grupo_desativado

Schema esperado da tabela `audit_logs`:
    id           uuid DEFAULT gen_random_uuid() PRIMARY KEY
    tenant_id    uuid REFERENCES tenants(id) ON DELETE CASCADE
    user_id      uuid  -- quem executou (pode ser NULL para falhas pré-login)
    actor_email  text  -- e-mail do ator (desnormalizado para facilitar consultas)
    event        text NOT NULL  -- ex: "login_failure"
    target_type  text  -- ex: "user", "equipment"
    target_id    text  -- id do objeto afetado
    metadata     jsonb -- dados extras (ip, role anterior, etc.)
    created_at   timestamptz DEFAULT now()

Índices recomendados:
    CREATE INDEX ON audit_logs (tenant_id, created_at DESC);
    CREATE INDEX ON audit_logs (user_id, created_at DESC);
    CREATE INDEX ON audit_logs (event, created_at DESC);

Uso:
    from src.auth.audit import audit_log

    audit_log(
        event="user_created",
        target_type="user",
        target_id=new_user_id,
        metadata={"email": email, "role": role},
    )
"""
from __future__ import annotations

import logging
from typing import Any

import streamlit as st

log = logging.getLogger("audit")


def _get_svc():
    """Obtém service client (lazy import para evitar circular)."""
    try:
        from src.db.supabase_client import get_supabase_service
        return get_supabase_service()
    except Exception:
        return None


def audit_log(
    event: str,
    *,
    tenant_id: str | None = None,
    user_id: str | None = None,
    actor_email: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Registra um evento de auditoria.

    Nunca levanta exceção — falha silenciosa com log local para não
    interromper a operação principal em caso de problema de rede.

    Args:
        event: Identificador do evento (snake_case, ex: "login_failure").
        tenant_id: Tenant afetado. Lido do session_state se omitido.
        user_id: Usuário ator. Lido do session_state se omitido.
        actor_email: E-mail desnormalizado para legibilidade nos relatórios.
        target_type: Tipo do objeto afetado ("user", "equipment", etc.).
        target_id: ID do objeto afetado.
        metadata: Dados adicionais livres (não inclua senhas ou tokens).
    """
    # Resolve valores do session_state quando não fornecidos
    resolved_tenant = tenant_id or st.session_state.get(
        "current_tenant_id") or None
    resolved_user = user_id or st.session_state.get("sb_user_id") or None

    record = {
        "tenant_id": resolved_tenant,
        "user_id": resolved_user,
        "actor_email": actor_email,
        "event": event,
        "target_type": target_type,
        "target_id": str(target_id) if target_id is not None else None,
        "metadata": metadata or {},
    }

    # Log local sempre (para depuração mesmo sem banco)
    log.info(
        "AUDIT event=%s tenant=%s user=%s target=%s/%s meta=%s",
        event,
        resolved_tenant,
        resolved_user,
        target_type,
        target_id,
        metadata,
    )

    # Persiste no Supabase de forma assíncrona-segura
    try:
        svc = _get_svc()
        if svc is None:
            return
        # Remove chaves None para não sobrescrever defaults do banco
        payload = {k: v for k, v in record.items() if v is not None}
        svc.table("audit_logs").insert(payload).execute()
    except Exception as exc:
        # Falha de escrita não deve derrubar a operação principal
        log.warning("Falha ao persistir audit_log event=%s: %s", event, exc)


# ── Helpers semânticos para os eventos mais comuns ──────────────────────

def audit_login_success(email: str, user_id: str) -> None:
    audit_log(
        "login_success",
        user_id=user_id,
        actor_email=email,
        metadata={"email": email},
    )


def audit_login_failure(email: str) -> None:
    audit_log(
        "login_failure",
        actor_email=email,
        metadata={"email": email},
    )


def audit_login_blocked(email: str, wait_secs: int) -> None:
    audit_log(
        "login_blocked",
        actor_email=email,
        metadata={"email": email, "blocked_for_seconds": wait_secs},
    )


def audit_logout(user_id: str | None = None) -> None:
    audit_log("logout", user_id=user_id)


def audit_user_created(new_user_id: str, email: str, role: str) -> None:
    audit_log(
        "user_created",
        target_type="user",
        target_id=new_user_id,
        metadata={"email": email, "role": role},
    )


def audit_user_deleted(deleted_user_id: str, email: str) -> None:
    audit_log(
        "user_deleted",
        target_type="user",
        target_id=deleted_user_id,
        metadata={"email": email},
    )


def audit_user_role_changed(
        target_user_id: str,
        old_role: str,
        new_role: str) -> None:
    audit_log(
        "user_role_changed",
        target_type="user",
        target_id=target_user_id,
        metadata={"old_role": old_role, "new_role": new_role},
    )


def audit_equipment_deleted(equipment_id: str, frota: str) -> None:
    audit_log(
        "equipment_deleted",
        target_type="equipment",
        target_id=equipment_id,
        metadata={"frota": frota},
    )


def audit_equipment_moved(
        equipment_id: str,
        from_group: str,
        to_group: str) -> None:
    audit_log(
        "equipment_moved",
        target_type="equipment",
        target_id=equipment_id,
        metadata={"from_group": from_group, "to_group": to_group},
    )


def audit_password_reset(email: str) -> None:
    audit_log(
        "password_reset_requested",
        actor_email=email,
        metadata={"email": email},
    )


# ── Departamentos ────────────────────────────────────────────────────────────

def audit_departamento_criado(dep_id: str, nome: str) -> None:
    audit_log(
        "departamento_criado",
        target_type="departamento",
        target_id=dep_id,
        metadata={"nome": nome},
    )


def audit_departamento_atualizado(dep_id: str, changes: dict) -> None:
    audit_log(
        "departamento_atualizado",
        target_type="departamento",
        target_id=dep_id,
        metadata=changes,
    )


def audit_departamento_deletado(dep_id: str, nome: str) -> None:
    audit_log(
        "departamento_deletado",
        target_type="departamento",
        target_id=dep_id,
        metadata={"nome": nome},
    )


def audit_departamento_toggle(dep_id: str, nome: str, ativo: bool) -> None:
    audit_log(
        "departamento_ativado" if ativo else "departamento_desativado",
        target_type="departamento",
        target_id=dep_id,
        metadata={"nome": nome, "ativo": ativo},
    )


# ── Grupos ───────────────────────────────────────────────────────────────────

def audit_grupo_criado(grupo_id: str, nome: str,
                       departamento_id: str | None = None) -> None:
    audit_log(
        "grupo_criado",
        target_type="grupo",
        target_id=grupo_id,
        metadata={"nome": nome, "departamento_id": departamento_id},
    )


def audit_grupo_atualizado(grupo_id: str, nome: str, changes: dict) -> None:
    audit_log(
        "grupo_atualizado",
        target_type="grupo",
        target_id=grupo_id,
        metadata={"nome": nome, **changes},
    )


def audit_grupo_deletado(grupo_id: str, nome: str,
                         equipamentos_desvinculados: int = 0) -> None:
    audit_log(
        "grupo_deletado",
        target_type="grupo",
        target_id=grupo_id,
        metadata={"nome": nome,
                  "equipamentos_desvinculados": equipamentos_desvinculados},
    )


def audit_grupo_toggle(grupo_id: str, nome: str, ativo: bool) -> None:
    audit_log(
        "grupo_ativado" if ativo else "grupo_desativado",
        target_type="grupo",
        target_id=grupo_id,
        metadata={"nome": nome, "ativo": ativo},
    )
