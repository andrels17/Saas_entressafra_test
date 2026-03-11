"""Busca responsáveis por departamento para disparo de e-mail.

Fluxo de dados:
  tenant_user_departamentos → user_id → auth.users (email) + user_profiles (nome)
  departamentos → equip_grupos → equipamentos → tarefas_servico

Retorna uma lista de RecipientGroup, um por departamento, com
os responsáveis (nome + email) e os dados já agregados para o PDF.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.db.supabase_client import get_supabase_service


@dataclass
class Recipient:
    user_id: str
    email: str
    nome: str


@dataclass
class RecipientGroup:
    """Um departamento e seus responsáveis + snapshot de dados."""
    departamento_id: str
    departamento_nome: str
    recipients: list[Recipient]
    grupo_ids: list[str]          # grupos pertencentes ao departamento
    dados: dict[str, Any]         # payload de dados para o PDF (preenchido pelo dispatcher)


def get_recipient_groups(tenant_id: str) -> list[RecipientGroup]:
    """
    Para cada departamento ativo, busca:
      - Responsáveis (role manager ou admin) vinculados ao departamento
      - Emails via auth.admin.list_users
      - Grupos pertencentes ao departamento
    Retorna apenas departamentos que têm ao menos um responsável com e-mail válido.
    """
    svc = get_supabase_service()

    # 1. Departamentos ativos
    deps = (
        svc.table("departamentos")
        .select("id,nome")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .execute()
        .data
    ) or []

    if not deps:
        return []

    dep_map = {d["id"]: d["nome"] for d in deps}

    # 2. Grupos por departamento
    grupos = (
        svc.table("equip_grupos")
        .select("id,departamento_id")
        .eq("tenant_id", tenant_id)
        .execute()
        .data
    ) or []
    dep_to_grupos: dict[str, list[str]] = {}
    for g in grupos:
        did = g.get("departamento_id")
        if did:
            dep_to_grupos.setdefault(did, []).append(g["id"])

    # 3. Vínculos usuário → departamento (apenas managers e admins)
    try:
        links = (
            svc.table("tenant_user_departamentos")
            .select("user_id,departamento_id")
            .eq("tenant_id", tenant_id)
            .execute()
            .data
        ) or []
    except Exception:
        links = []

    # Filtra apenas roles manager/admin
    try:
        tu_rows = (
            svc.table("tenant_users")
            .select("user_id,role")
            .eq("tenant_id", tenant_id)
            .in_("role", ["admin", "manager", "gestor"])
            .execute()
            .data
        ) or []
    except Exception:
        tu_rows = []
    manager_ids = {r["user_id"] for r in tu_rows}

    # Mapa: departamento_id → set[user_id] (gestores)
    dep_to_users: dict[str, set[str]] = {}
    for lk in links:
        uid = lk.get("user_id")
        did = lk.get("departamento_id")
        if uid and did and uid in manager_ids:
            dep_to_users.setdefault(did, set()).add(uid)

    # 4. Busca e-mails e nomes
    all_user_ids = {uid for uids in dep_to_users.values() for uid in uids}
    user_info: dict[str, Recipient] = {}

    # Nomes via user_profiles
    if all_user_ids:
        try:
            profiles = (
                svc.table("user_profiles")
                .select("user_id,nome")
                .in_("user_id", list(all_user_ids))
                .execute()
                .data
            ) or []
            for p in profiles:
                uid = p.get("user_id", "")
                user_info[uid] = Recipient(
                    user_id=uid,
                    email="",            # preenchido abaixo
                    nome=p.get("nome") or uid[:8],
                )
        except Exception:
            pass

    # E-mails via auth.admin (requer service role)
    try:
        # Supabase Python SDK: admin.list_users() retorna paginado
        page = 1
        while True:
            resp = svc.auth.admin.list_users(page=page, per_page=1000)
            users_page = resp if isinstance(resp, list) else getattr(resp, "users", [])
            if not users_page:
                break
            for u in users_page:
                uid = getattr(u, "id", None) or (u.get("id") if isinstance(u, dict) else None)
                email = getattr(u, "email", None) or (u.get("email") if isinstance(u, dict) else None)
                if uid and email and uid in all_user_ids:
                    if uid not in user_info:
                        user_info[uid] = Recipient(user_id=uid, email=email, nome=uid[:8])
                    else:
                        user_info[uid].email = email
            if len(users_page) < 1000:
                break
            page += 1
    except Exception:
        pass

    # 5. Monta RecipientGroups
    groups: list[RecipientGroup] = []
    for dep_id, dep_nome in dep_map.items():
        uids = dep_to_users.get(dep_id, set())
        recipients = [
            r for uid in uids
            if (r := user_info.get(uid)) and r.email
        ]
        if not recipients:
            continue
        groups.append(RecipientGroup(
            departamento_id=dep_id,
            departamento_nome=dep_nome,
            recipients=recipients,
            grupo_ids=dep_to_grupos.get(dep_id, []),
            dados={},
        ))

    return groups


def get_admin_recipients(tenant_id: str) -> list[Recipient]:
    """Retorna todos os admins do tenant (para relatório geral)."""
    svc = get_supabase_service()
    try:
        tu_rows = (
            svc.table("tenant_users")
            .select("user_id,role")
            .eq("tenant_id", tenant_id)
            .eq("role", "admin")
            .execute()
            .data
        ) or []
    except Exception:
        return []

    admin_ids = [r["user_id"] for r in tu_rows]
    if not admin_ids:
        return []

    profiles = {}
    try:
        prows = (
            svc.table("user_profiles")
            .select("user_id,nome")
            .in_("user_id", admin_ids)
            .execute()
            .data
        ) or []
        profiles = {p["user_id"]: p.get("nome", "") for p in prows}
    except Exception:
        pass

    recipients = []
    try:
        page = 1
        while True:
            resp = svc.auth.admin.list_users(page=page, per_page=1000)
            users_page = resp if isinstance(resp, list) else getattr(resp, "users", [])
            if not users_page:
                break
            for u in users_page:
                uid   = getattr(u, "id", None) or (u.get("id") if isinstance(u, dict) else None)
                email = getattr(u, "email", None) or (u.get("email") if isinstance(u, dict) else None)
                if uid and email and uid in admin_ids:
                    recipients.append(Recipient(
                        user_id=uid,
                        email=email,
                        nome=profiles.get(uid) or uid[:8],
                    ))
            if len(users_page) < 1000:
                break
            page += 1
    except Exception:
        pass

    return recipients
