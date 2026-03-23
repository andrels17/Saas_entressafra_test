"""Busca destinatários de e-mail com suporte a tipo de relatório.

Tipos de relatório:
  "gestor"    — PDF completo por departamento (gestores/coordenadores)
  "executivo" — PDF consolidado cross-departamentos (supervisores/diretores)

Lógica de tipo:
  1. Verifica preferência salva em `tenant_email_prefs` (override manual)
  2. Fallback pelo role: supervisor/admin → executivo, gestor → gestor

Tabela necessária no Supabase (criar se não existir):
  tenant_email_prefs(tenant_id, user_id, tipo_relatorio text, ativo bool)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.db.supabase_client import get_supabase_service

log = logging.getLogger("saas.recipients")

TIPO_GESTOR = "gestor"
TIPO_EXECUTIVO = "executivo"
ROLE_DEFAULT: dict[str, str] = {
    "gestor": TIPO_GESTOR,
    "supervisor": TIPO_EXECUTIVO,
    "admin": TIPO_EXECUTIVO,
    "superadmin": TIPO_EXECUTIVO,
}


@dataclass
class Recipient:
    user_id: str
    email: str
    nome: str
    tipo_relatorio: str = TIPO_GESTOR


@dataclass
class RecipientGroup:
    departamento_id: str
    departamento_nome: str
    recipients: list[Recipient]
    grupo_ids: list[str]
    dados: dict[str, Any] = field(default_factory=dict)


def _fetch_user_emails(svc, user_ids: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    if not user_ids:
        return out
    try:
        page = 1
        while True:
            resp = svc.auth.admin.list_users(page=page, per_page=1000)
            users_page = resp if isinstance(resp, list) else getattr(resp, "users", [])
            if not users_page:
                break
            for user in users_page:
                uid = getattr(user, "id", None) or (
                    user.get("id") if isinstance(user, dict) else None
                )
                email = getattr(user, "email", None) or (
                    user.get("email") if isinstance(user, dict) else None
                )
                if uid and email and uid in user_ids:
                    out[uid] = email
            if len(users_page) < 1000:
                break
            page += 1
    except Exception as exc:
        log.error(
            "_fetch_user_emails: falha ao listar usuários via auth.admin "
            "(tenant com %d user_ids esperados): %s",
            len(user_ids),
            exc,
        )
    return out


def _fetch_profiles(svc, user_ids: list[str]) -> dict[str, str]:
    if not user_ids:
        return {}
    try:
        rows = (
            svc.table("user_profiles")
            .select("user_id,nome")
            .in_("user_id", user_ids)
            .execute()
            .data
        ) or []
        return {row["user_id"]: row.get("nome") or "" for row in rows}
    except Exception as exc:
        log.warning("_fetch_profiles: falha ao buscar perfis: %s", exc)
        return {}


def _fetch_email_prefs(svc, tenant_id: str) -> dict[str, str]:
    """Retorna {user_id: tipo_relatorio}.

    Usuários com ativo=False ficam mapeados como 'nenhum' para serem excluídos.
    """
    try:
        rows = (
            svc.table("tenant_email_prefs")
            .select("user_id,tipo_relatorio,ativo")
            .eq("tenant_id", tenant_id)
            .execute()
            .data
        ) or []
        out: dict[str, str] = {}
        for row in rows:
            uid = row.get("user_id")
            tipo = row.get("tipo_relatorio") or ""
            ativo = row.get("ativo", True)
            if uid:
                out[uid] = tipo if ativo else "nenhum"
        return out
    except Exception as exc:
        log.warning("_fetch_email_prefs: falha ao buscar preferências: %s", exc)
        return {}


def _resolve_tipo(user_id: str, role: str, prefs: dict[str, str]) -> str:
    """Resolve tipo_relatorio. 'nenhum' = não enviar."""
    if user_id in prefs:
        return prefs[user_id]
    return ROLE_DEFAULT.get(role or "", TIPO_GESTOR)


def _nome_from(uid: str, profiles: dict[str, str], email: str) -> str:
    nome = profiles.get(uid) or ""
    if nome:
        return nome
    return email.split("@")[0].replace(".", " ").replace("_", " ").title()


def _fetch_active_departments(svc, tenant_id: str) -> list[dict[str, Any]]:
    """Busca departamentos ativos; se não existirem, usa grupos ativos como fallback."""
    deps = (
        svc.table("departamentos")
        .select("id,nome")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .execute()
        .data
    ) or []
    if deps:
        return deps

    grupos = (
        svc.table("equip_grupos")
        .select("id,nome,departamento_id")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .execute()
        .data
    ) or []
    if not grupos:
        return []

    by_dep: dict[str, dict[str, Any]] = {}
    sem_dep: list[dict[str, Any]] = []
    for grupo in grupos:
        dep_id = grupo.get("departamento_id")
        gid = grupo.get("id")
        nome = grupo.get("nome") or "Grupo sem nome"
        if dep_id:
            entry = by_dep.setdefault(
                dep_id,
                {
                    "id": dep_id,
                    "nome": f"Departamento {str(dep_id)[:8]}",
                    "_grupo_ids": [],
                },
            )
            if gid:
                entry["_grupo_ids"].append(gid)
        elif gid:
            sem_dep.append(
                {
                    "id": f"grupo:{gid}",
                    "nome": nome,
                    "_grupo_ids": [gid],
                }
            )

    return list(by_dep.values()) + sem_dep


def _fetch_groups_by_department(svc, tenant_id: str) -> dict[str, list[str]]:
    grupos = (
        svc.table("equip_grupos")
        .select("id,departamento_id")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .execute()
        .data
    ) or []

    dep_to_grupos: dict[str, list[str]] = {}
    for grupo in grupos:
        gid = grupo.get("id")
        dep_id = grupo.get("departamento_id")
        if gid and dep_id:
            dep_to_grupos.setdefault(dep_id, []).append(gid)
        elif gid and not dep_id:
            dep_to_grupos[f"grupo:{gid}"] = [gid]
    return dep_to_grupos


def get_recipient_groups(tenant_id: str) -> list[RecipientGroup]:
    """Retorna grupos de departamento com destinatários tipo gestor."""
    svc = get_supabase_service()
    prefs = _fetch_email_prefs(svc, tenant_id)

    deps = _fetch_active_departments(svc, tenant_id)
    if not deps:
        return []
    dep_map = {dep["id"]: dep["nome"] for dep in deps}
    dep_to_grupos = _fetch_groups_by_department(svc, tenant_id)

    try:
        links = (
            svc.table("tenant_user_departamentos")
            .select("user_id,departamento_id")
            .eq("tenant_id", tenant_id)
            .execute()
            .data
        ) or []
    except Exception as exc:
        log.warning("get_recipient_groups: falha ao buscar vínculos por departamento: %s", exc)
        links = []

    try:
        tu_rows = (
            svc.table("tenant_users")
            .select("user_id,role")
            .eq("tenant_id", tenant_id)
            .execute()
            .data
        ) or []
    except Exception as exc:
        log.warning("get_recipient_groups: falha ao buscar tenant_users: %s", exc)
        tu_rows = []
    user_roles = {row["user_id"]: row.get("role", "") for row in tu_rows if row.get("user_id")}

    dep_to_users: dict[str, set[str]] = {}
    for link in links:
        uid = link.get("user_id")
        dep_id = link.get("departamento_id")
        if not (uid and dep_id):
            continue
        role = user_roles.get(uid, "")
        if _resolve_tipo(uid, role, prefs) == TIPO_GESTOR:
            dep_to_users.setdefault(dep_id, set()).add(uid)

    all_uids = {uid for uids in dep_to_users.values() for uid in uids}
    profiles = _fetch_profiles(svc, list(all_uids))
    emails = _fetch_user_emails(svc, all_uids)

    groups: list[RecipientGroup] = []
    for dep_id, dep_nome in dep_map.items():
        uids = dep_to_users.get(dep_id, set())
        recipients: list[Recipient] = []
        for uid in uids:
            email = emails.get(uid, "")
            if not email:
                continue
            recipients.append(
                Recipient(
                    user_id=uid,
                    email=email,
                    nome=_nome_from(uid, profiles, email),
                    tipo_relatorio=TIPO_GESTOR,
                )
            )

        grupo_ids = dep_to_grupos.get(dep_id) or next(
            (dep.get("_grupo_ids", []) for dep in deps if dep["id"] == dep_id),
            [],
        )
        if not recipients:
            continue
        groups.append(
            RecipientGroup(
                departamento_id=dep_id,
                departamento_nome=dep_nome,
                recipients=recipients,
                grupo_ids=grupo_ids,
            )
        )
    return groups


def get_executive_recipients(tenant_id: str) -> list[Recipient]:
    """Retorna destinatários do relatório executivo."""
    svc = get_supabase_service()
    prefs = _fetch_email_prefs(svc, tenant_id)

    try:
        tu_rows = (
            svc.table("tenant_users")
            .select("user_id,role")
            .eq("tenant_id", tenant_id)
            .execute()
            .data
        ) or []
    except Exception as exc:
        log.warning("get_executive_recipients: falha ao buscar tenant_users: %s", exc)
        return []

    exec_uids: set[str] = set()
    for row in tu_rows:
        uid = row.get("user_id", "")
        role = row.get("role", "")
        if _resolve_tipo(uid, role, prefs) == TIPO_EXECUTIVO:
            exec_uids.add(uid)

    if not exec_uids:
        return []

    profiles = _fetch_profiles(svc, list(exec_uids))
    emails = _fetch_user_emails(svc, exec_uids)
    return [
        Recipient(
            user_id=uid,
            email=emails.get(uid, ""),
            nome=_nome_from(uid, profiles, emails.get(uid, "")),
            tipo_relatorio=TIPO_EXECUTIVO,
        )
        for uid in exec_uids
        if emails.get(uid)
    ]


def get_admin_recipients(tenant_id: str) -> list[Recipient]:
    """Compat retroativa — retorna executivos."""
    return get_executive_recipients(tenant_id)


def save_email_pref(
    tenant_id: str,
    user_id: str,
    tipo_relatorio: str,
    ativo: bool = True,
) -> bool:
    """Salva override manual de tipo de relatório para um usuário."""
    svc = get_supabase_service()
    try:
        svc.table("tenant_email_prefs").upsert(
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "tipo_relatorio": tipo_relatorio,
                "ativo": False if tipo_relatorio == "nenhum" else ativo,
            },
            on_conflict="tenant_id,user_id",
        ).execute()
        return True
    except Exception as exc:
        log.error(
            "save_email_pref: falha ao salvar preferência user=%s tenant=%s tipo=%s: %s",
            user_id,
            tenant_id,
            tipo_relatorio,
            exc,
        )
        return False


def get_all_users_with_prefs(tenant_id: str) -> list[dict[str, Any]]:
    """Todos os usuários do tenant com role, email e tipo_relatorio resolvido."""
    svc = get_supabase_service()
    prefs = _fetch_email_prefs(svc, tenant_id)

    try:
        tu_rows = (
            svc.table("tenant_users")
            .select("user_id,role")
            .eq("tenant_id", tenant_id)
            .execute()
            .data
        ) or []
    except Exception:
        return []

    all_uids = {row["user_id"] for row in tu_rows if row.get("user_id")}
    profiles = _fetch_profiles(svc, list(all_uids))
    emails = _fetch_user_emails(svc, all_uids)

    result: list[dict[str, Any]] = []
    for row in tu_rows:
        uid = row.get("user_id", "")
        role = row.get("role", "")
        email = emails.get(uid, "")
        if not email:
            continue
        result.append(
            {
                "user_id": uid,
                "nome": _nome_from(uid, profiles, email),
                "email": email,
                "role": role,
                "tipo_relatorio": _resolve_tipo(uid, role, prefs),
                "override": uid in prefs,
            }
        )
    return sorted(result, key=lambda item: (item["role"], item["nome"]))


def _build_all_dept_groups(tenant_id: str) -> list[RecipientGroup]:
    """Retorna RecipientGroup para todos os departamentos ativos do tenant.

    Se não houver departamentos ativos, usa grupos ativos como fallback.
    """
    svc = get_supabase_service()
    deps = _fetch_active_departments(svc, tenant_id)
    if not deps:
        return []

    dep_to_grupos = _fetch_groups_by_department(svc, tenant_id)
    return [
        RecipientGroup(
            departamento_id=dep["id"],
            departamento_nome=dep["nome"],
            recipients=[],
            grupo_ids=dep_to_grupos.get(dep["id"]) or dep.get("_grupo_ids", []),
        )
        for dep in deps
    ]
