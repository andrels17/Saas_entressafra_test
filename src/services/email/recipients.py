"""Busca destinatários de e-mail com suporte a tipo de relatório.

Tipos de relatório:
  "gestor"    — PDF completo por departamento (gestores/coordenadores)
  "executivo" — PDF consolidado cross-departamentos (supervisores/diretores)

Lógica de tipo:
  1. Verifica preferência salva em `tenant_email_prefs` (override manual)
  2. Fallback pelo role: supervisor/admin → executivo, gestor → gestor
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
            for u in users_page:
                uid = getattr(u, "id", None) or (u.get("id") if isinstance(u, dict) else None)
                email = getattr(u, "email", None) or (u.get("email") if isinstance(u, dict) else None)
                if uid and email and uid in user_ids:
                    out[uid] = email
            if len(users_page) < 1000:
                break
            page += 1
    except Exception as exc:
        log.error(
            "_fetch_user_emails: falha ao listar usuários via auth.admin "
            "(tenant com %d user_ids esperados): %s",
            len(user_ids), exc,
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
        return {r["user_id"]: r.get("nome") or "" for r in rows}
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
        out = {}
        for r in rows:
            uid = r.get("user_id")
            tipo = r.get("tipo_relatorio") or ""
            ativo = r.get("ativo", True)
            if uid:
                out[uid] = tipo if ativo else "nenhum"
        print("DEBUG prefs:", rows)
        return out
    except Exception as exc:
        log.warning("_fetch_email_prefs: falha ao buscar preferências: %s", exc)
        print("DEBUG prefs error:", repr(exc))
        return {}


def _resolve_tipo(user_id: str, role: str, prefs: dict[str, str]) -> str:
    """Resolve tipo_relatorio. 'nenhum' = não enviar (override explícito)."""
    if user_id in prefs:
        return prefs[user_id]
    return ROLE_DEFAULT.get(role or "", TIPO_GESTOR)


def _nome_from(uid: str, profiles: dict, email: str) -> str:
    nome = profiles.get(uid) or ""
    if nome:
        return nome
    return email.split("@")[0].replace(".", " ").replace("_", " ").title()


def _fetch_departamentos_ativos(svc, tenant_id: str) -> list[dict[str, Any]]:
    """Busca departamentos ativos; se não existirem, cria fallback por grupo.

    Isso evita que o scheduler inteiro pule o tenant só porque a tabela
    `departamentos` está vazia/inativa, desde que existam grupos ativos.
    """
    deps = (
        svc.table("departamentos")
        .select("id,nome")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .execute()
        .data
    ) or []
    print("DEBUG departamentos:", deps)

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
    print("DEBUG grupos fallback:", grupos)

    # Agrupa por departamento_id quando existir
    by_dep: dict[str, dict[str, Any]] = {}
    sem_dep: list[dict[str, Any]] = []
    for g in grupos:
        dep_id = g.get("departamento_id")
        nome = g.get("nome") or "Grupo sem nome"
        gid = g.get("id")
        if dep_id:
            entry = by_dep.setdefault(dep_id, {
                "id": dep_id,
                "nome": f"Departamento {dep_id[:8]}",
                "_grupo_ids": [],
            })
            if gid:
                entry["_grupo_ids"].append(gid)
        else:
            # Sem departamento: cria um pseudo-departamento por grupo
            if gid:
                sem_dep.append({
                    "id": f"grupo:{gid}",
                    "nome": nome,
                    "_grupo_ids": [gid],
                })

    fallback = list(by_dep.values()) + sem_dep
    print("DEBUG departamentos fallback result:", fallback)
    return fallback


def _fetch_grupos_por_departamento(svc, tenant_id: str) -> dict[str, list[str]]:
    grupos = (
        svc.table("equip_grupos")
        .select("id,departamento_id,nome")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .execute()
        .data
    ) or []
    print("DEBUG grupos:", grupos)

    dep_to_grupos: dict[str, list[str]] = {}
    for g in grupos:
        gid = g.get("id")
        did = g.get("departamento_id")
        if gid and did:
            dep_to_grupos.setdefault(did, []).append(gid)
        elif gid and not did:
            dep_to_grupos[f"grupo:{gid}"] = [gid]
    print("DEBUG dep_to_grupos:", dep_to_grupos)
    return dep_to_grupos


def get_recipient_groups(tenant_id: str) -> list[RecipientGroup]:
    """Retorna grupos de departamento com destinatários tipo gestor."""
    print("🔍 get_recipient_groups tenant:", tenant_id)
    svc = get_supabase_service()
    prefs = _fetch_email_prefs(svc, tenant_id)

    deps = _fetch_departamentos_ativos(svc, tenant_id)
    if not deps:
        print("DEBUG get_recipient_groups: nenhum departamento/grupo ativo")
        return []

    dep_map = {d["id"]: d["nome"] for d in deps}
    dep_to_grupos = _fetch_grupos_por_departamento(svc, tenant_id)

    try:
        links = (
            svc.table("tenant_user_departamentos")
            .select("user_id,departamento_id")
            .eq("tenant_id", tenant_id)
            .execute()
            .data
        ) or []
    except Exception as exc:
        print("DEBUG tenant_user_departamentos error:", repr(exc))
        links = []
    print("DEBUG tenant_user_departamentos:", links)

    try:
        tu_rows = (
            svc.table("tenant_users")
            .select("user_id,role")
            .eq("tenant_id", tenant_id)
            .execute()
            .data
        ) or []
    except Exception as exc:
        print("DEBUG tenant_users error:", repr(exc))
        tu_rows = []
    print("DEBUG tenant_users:", tu_rows)

    user_roles = {r["user_id"]: r.get("role", "") for r in tu_rows if r.get("user_id")}

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

    # Fallback opcional: se não houver vínculos por departamento, usa prefs/roles
    # apenas para debug, sem inventar associação errada entre usuário e depto.
    print("DEBUG dep_to_users:", {k: sorted(v) for k, v in dep_to_users.items()})

    all_uids = {uid for uids in dep_to_users.values() for uid in uids}
    print("DEBUG all_uids gestor:", sorted(all_uids))
    profiles = _fetch_profiles(svc, list(all_uids))
    emails = _fetch_user_emails(svc, all_uids)
    print("DEBUG emails gestor:", emails)

    groups: list[RecipientGroup] = []
    for dep_id, dep_nome in dep_map.items():
        uids = dep_to_users.get(dep_id, set())
        recipients = []
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
            (d.get("_grupo_ids", []) for d in deps if d["id"] == dep_id),
            [],
        )

        if not recipients:
            # mantém sem recipients? não; gestor só faz sentido com destinatário
            continue

        groups.append(
            RecipientGroup(
                departamento_id=dep_id,
                departamento_nome=dep_nome,
                recipients=recipients,
                grupo_ids=grupo_ids,
            )
        )

    print(
        "DEBUG get_recipient_groups result:",
        [
            {
                "departamento_id": g.departamento_id,
                "departamento_nome": g.departamento_nome,
                "grupo_ids": g.grupo_ids,
                "recipients": [r.email for r in g.recipients],
            }
            for g in groups
        ],
    )
    return groups


def get_executive_recipients(tenant_id: str) -> list[Recipient]:
    """Retorna destinatários do relatório executivo."""
    print("🔍 get_executive_recipients tenant:", tenant_id)
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
        print("DEBUG executive tenant_users error:", repr(exc))
        return []

    print("DEBUG executive tenant_users:", tu_rows)

    exec_uids: set[str] = set()
    for r in tu_rows:
        uid = r.get("user_id", "")
        role = r.get("role", "")
        if _resolve_tipo(uid, role, prefs) == TIPO_EXECUTIVO:
            exec_uids.add(uid)

    print("DEBUG executive user_ids:", sorted(exec_uids))

    if not exec_uids:
        return []

    profiles = _fetch_profiles(svc, list(exec_uids))
    emails = _fetch_user_emails(svc, exec_uids)
    print("DEBUG executive emails:", emails)

    recipients = [
        Recipient(
            user_id=uid,
            email=emails.get(uid, ""),
            nome=_nome_from(uid, profiles, emails.get(uid, "")),
            tipo_relatorio=TIPO_EXECUTIVO,
        )
        for uid in exec_uids
        if emails.get(uid)
    ]
    print("DEBUG executive recipients:", [r.email for r in recipients])
    return recipients


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
            "save_email_pref: falha ao salvar preferência "
            "user=%s tenant=%s tipo=%s: %s",
            user_id,
            tenant_id,
            tipo_relatorio,
            exc,
        )
        return False


def get_all_users_with_prefs(tenant_id: str) -> list[dict]:
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
    except Exception as exc:
        print("DEBUG get_all_users_with_prefs tenant_users error:", repr(exc))
        return []

    all_uids = {r["user_id"] for r in tu_rows if r.get("user_id")}
    profiles = _fetch_profiles(svc, list(all_uids))
    emails = _fetch_user_emails(svc, all_uids)

    result = []
    for r in tu_rows:
        uid = r.get("user_id", "")
        role = r.get("role", "")
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
    return sorted(result, key=lambda x: (x["role"], x["nome"]))


def _build_all_dept_groups(tenant_id: str) -> list[RecipientGroup]:
    """Retorna RecipientGroup para TODOS os departamentos ativos do tenant.

    Se não houver departamentos ativos, faz fallback para grupos ativos.
    """
    print("🔍 _build_all_dept_groups tenant:", tenant_id)
    svc = get_supabase_service()

    deps = _fetch_departamentos_ativos(svc, tenant_id)
    if not deps:
        print("DEBUG _build_all_dept_groups: vazio")
        return []

    dep_to_grupos = _fetch_grupos_por_departamento(svc, tenant_id)

    result = [
        RecipientGroup(
            departamento_id=d["id"],
            departamento_nome=d["nome"],
            recipients=[],
            grupo_ids=dep_to_grupos.get(d["id"]) or d.get("_grupo_ids", []),
        )
        for d in deps
    ]
    print(
        "DEBUG _build_all_dept_groups result:",
        [
            {
                "departamento_id": g.departamento_id,
                "departamento_nome": g.departamento_nome,
                "grupo_ids": g.grupo_ids,
            }
            for g in result
        ],
    )
    return result
