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

from dataclasses import dataclass, field
from typing import Any

from src.db.supabase_client import get_supabase_service

TIPO_GESTOR     = "gestor"
TIPO_EXECUTIVO  = "executivo"
ROLE_DEFAULT: dict[str, str] = {
    "gestor":     TIPO_GESTOR,
    "supervisor": TIPO_EXECUTIVO,
    "admin":      TIPO_EXECUTIVO,
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
            for u in users_page:
                uid   = getattr(u, "id", None) or (u.get("id") if isinstance(u, dict) else None)
                email = getattr(u, "email", None) or (u.get("email") if isinstance(u, dict) else None)
                if uid and email and uid in user_ids:
                    out[uid] = email
            if len(users_page) < 1000:
                break
            page += 1
    except Exception:
        pass
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
        return {r["user_id"]: r.get("nome") or "" for r in rows}
    except Exception:
        return {}


def _fetch_email_prefs(svc, tenant_id: str) -> dict[str, str]:
    try:
        rows = (
            svc.table("tenant_email_prefs")
            .select("user_id,tipo_relatorio,ativo")
            .eq("tenant_id", tenant_id)
            .eq("ativo", True)
            .execute()
            .data
        ) or []
        return {r["user_id"]: r["tipo_relatorio"] for r in rows if r.get("tipo_relatorio")}
    except Exception:
        return {}


def _resolve_tipo(user_id: str, role: str, prefs: dict[str, str]) -> str:
    if user_id in prefs:
        return prefs[user_id]
    return ROLE_DEFAULT.get(role or "", TIPO_GESTOR)


def _nome_from(uid: str, profiles: dict, email: str) -> str:
    nome = profiles.get(uid) or ""
    if nome:
        return nome
    return email.split("@")[0].replace(".", " ").replace("_", " ").title()


def get_recipient_groups(tenant_id: str) -> list[RecipientGroup]:
    """Retorna grupos de departamento com destinatários tipo gestor."""
    svc = get_supabase_service()
    prefs = _fetch_email_prefs(svc, tenant_id)

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

    try:
        tu_rows = (
            svc.table("tenant_users")
            .select("user_id,role")
            .eq("tenant_id", tenant_id)
            .execute()
            .data
        ) or []
    except Exception:
        tu_rows = []
    user_roles = {r["user_id"]: r.get("role", "") for r in tu_rows}

    dep_to_users: dict[str, set[str]] = {}
    for lk in links:
        uid = lk.get("user_id")
        did = lk.get("departamento_id")
        if not (uid and did):
            continue
        role = user_roles.get(uid, "")
        tipo = _resolve_tipo(uid, role, prefs)
        if tipo == TIPO_GESTOR:
            dep_to_users.setdefault(did, set()).add(uid)

    all_uids = {uid for uids in dep_to_users.values() for uid in uids}
    profiles = _fetch_profiles(svc, list(all_uids))
    emails   = _fetch_user_emails(svc, all_uids)

    groups: list[RecipientGroup] = []
    for dep_id, dep_nome in dep_map.items():
        uids = dep_to_users.get(dep_id, set())
        recipients = []
        for uid in uids:
            email = emails.get(uid, "")
            if not email:
                continue
            recipients.append(Recipient(
                user_id=uid, email=email,
                nome=_nome_from(uid, profiles, email),
                tipo_relatorio=TIPO_GESTOR,
            ))
        if not recipients:
            continue
        groups.append(RecipientGroup(
            departamento_id=dep_id,
            departamento_nome=dep_nome,
            recipients=recipients,
            grupo_ids=dep_to_grupos.get(dep_id, []),
        ))
    return groups


def get_executive_recipients(tenant_id: str) -> list[Recipient]:
    """Retorna destinatários do relatório executivo (supervisores, admins, overrides)."""
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

    exec_uids: set[str] = set()
    for r in tu_rows:
        uid  = r.get("user_id", "")
        role = r.get("role", "")
        if _resolve_tipo(uid, role, prefs) == TIPO_EXECUTIVO:
            exec_uids.add(uid)

    if not exec_uids:
        return []

    profiles = _fetch_profiles(svc, list(exec_uids))
    emails   = _fetch_user_emails(svc, exec_uids)

    return [
        Recipient(
            user_id=uid, email=emails.get(uid, ""),
            nome=_nome_from(uid, profiles, emails.get(uid, "")),
            tipo_relatorio=TIPO_EXECUTIVO,
        )
        for uid in exec_uids if emails.get(uid)
    ]


def get_admin_recipients(tenant_id: str) -> list[Recipient]:
    """Compat retroativa — retorna executivos."""
    return get_executive_recipients(tenant_id)


def save_email_pref(tenant_id: str, user_id: str, tipo_relatorio: str, ativo: bool = True) -> bool:
    """Salva override manual de tipo de relatório para um usuário."""
    svc = get_supabase_service()
    try:
        svc.table("tenant_email_prefs").upsert({
            "tenant_id":      tenant_id,
            "user_id":        user_id,
            "tipo_relatorio": tipo_relatorio,
            "ativo":          ativo,
        }, on_conflict="tenant_id,user_id").execute()
        return True
    except Exception:
        return False


def get_all_users_with_prefs(tenant_id: str) -> list[dict]:
    """Todos os usuários do tenant com role, email e tipo_relatorio resolvido.
    Usado pela UI de configuração de destinatários.
    """
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

    all_uids = {r["user_id"] for r in tu_rows}
    profiles = _fetch_profiles(svc, list(all_uids))
    emails   = _fetch_user_emails(svc, all_uids)

    result = []
    for r in tu_rows:
        uid   = r.get("user_id", "")
        role  = r.get("role", "")
        email = emails.get(uid, "")
        if not email:
            continue
        result.append({
            "user_id":        uid,
            "nome":           _nome_from(uid, profiles, email),
            "email":          email,
            "role":           role,
            "tipo_relatorio": _resolve_tipo(uid, role, prefs),
            "override":       uid in prefs,
        })
    return sorted(result, key=lambda x: (x["role"], x["nome"]))


def _build_all_dept_groups(tenant_id: str) -> list[RecipientGroup]:
    """Retorna RecipientGroup para TODOS os departamentos ativos do tenant,
    independente de terem gestores vinculados. Usado pelo relatório executivo
    para garantir que todos os deptos apareçam no PDF consolidado.
    """
    svc = get_supabase_service()

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

    return [
        RecipientGroup(
            departamento_id=d["id"],
            departamento_nome=d["nome"],
            recipients=[],          # sem destinatários — só para montar snapshot
            grupo_ids=dep_to_grupos.get(d["id"], []),
        )
        for d in deps
    ]
