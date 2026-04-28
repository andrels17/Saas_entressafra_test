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
    "manager": TIPO_GESTOR,   # role salvo no banco como "manager" (gestor)
    "gestor": TIPO_GESTOR,    # compatibilidade retroativa com strings legadas
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
    # Importante: NÃO filtrar apenas grupos ativos aqui.
    # O dashboard executivo precisa enxergar também equipamentos/tarefas
    # vinculados a grupos hoje inativos, para não zerar departamentos que
    # tiveram movimentação real na revisão.
    grupos = (
        svc.table("equip_grupos")
        .select("id,departamento_id")
        .eq("tenant_id", tenant_id)
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
    """Retorna grupos de destinatários respeitando vínculos por grupo.

    Compatibilidade para fluxos antigos:
    - se o usuário está vinculado a grupos específicos, o e-mail/PDF usa
      somente esses grupos;
    - se está vinculado só ao departamento, usa todos os grupos do departamento;
    - usuários com o mesmo departamento e o mesmo conjunto de grupos são
      agrupados no mesmo RecipientGroup para evitar e-mails duplicados.
    """
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
            .select("user_id,departamento_id,grupo_id")
            .eq("tenant_id", tenant_id)
            .execute()
            .data
        ) or []
    except Exception as exc:
        log.warning("get_recipient_groups: falha ao buscar vínculos: %s", exc)
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

    user_roles = {
        row["user_id"]: row.get("role", "")
        for row in tu_rows
        if row.get("user_id")
    }

    uid_to_deps: dict[str, set[str]] = {}
    uid_dep_to_explicit_grps: dict[str, dict[str, set[str]]] = {}

    for link in links:
        uid = link.get("user_id")
        dep_id = link.get("departamento_id")
        grp_id = link.get("grupo_id")
        if not (uid and dep_id):
            continue
        role = user_roles.get(uid, "")
        if _resolve_tipo(uid, role, prefs) != TIPO_GESTOR:
            continue

        uid_to_deps.setdefault(uid, set()).add(dep_id)
        if grp_id:
            uid_dep_to_explicit_grps.setdefault(uid, {}).setdefault(dep_id, set()).add(grp_id)

    all_uids = set(uid_to_deps.keys())
    profiles = _fetch_profiles(svc, list(all_uids))
    emails = _fetch_user_emails(svc, all_uids)

    # (departamento_id, grupo_ids_tuple) → recipients
    buckets: dict[tuple[str, tuple[str, ...]], list[Recipient]] = {}
    seen_in_bucket: dict[tuple[str, tuple[str, ...]], set[str]] = {}

    for uid, dep_ids in uid_to_deps.items():
        email = emails.get(uid, "")
        if not email:
            continue

        recipient = Recipient(
            user_id=uid,
            email=email,
            nome=_nome_from(uid, profiles, email),
            tipo_relatorio=TIPO_GESTOR,
        )

        for dep_id in sorted(dep_ids):
            explicit = uid_dep_to_explicit_grps.get(uid, {}).get(dep_id, set())
            grupo_ids = sorted({str(g) for g in (explicit or dep_to_grupos.get(dep_id, [])) if g})
            if not grupo_ids:
                continue

            key = (dep_id, tuple(grupo_ids))
            seen = seen_in_bucket.setdefault(key, set())
            if email in seen:
                continue
            seen.add(email)
            buckets.setdefault(key, []).append(recipient)

    groups: list[RecipientGroup] = []
    for (dep_id, grupo_ids_tuple), recipients in buckets.items():
        if not recipients:
            continue
        groups.append(
            RecipientGroup(
                departamento_id=dep_id,
                departamento_nome=dep_map.get(dep_id, dep_id),
                recipients=recipients,
                grupo_ids=list(grupo_ids_tuple),
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


@dataclass
class ManagerPdfBundle:
    """Gestor com todos os departamentos aos quais está vinculado.

    O dispatcher gera um PDF por departamento e os agrupa num único e-mail
    para cada gestor, evitando N e-mails separados quando o gestor cobre
    múltiplos departamentos.
    """
    recipient: Recipient
    departamento_ids: list[str]
    departamento_nomes: list[str]
    grupo_ids_por_dept: dict[str, list[str]]  # dep_id → grupo_ids



def get_manager_pdf_bundles(tenant_id: str) -> list[ManagerPdfBundle]:
    """Retorna um bundle por gestor respeitando departamentos e grupos vinculados.

    Fonte oficial dos vínculos:
      - tenant_user_departamentos(departamento_id, grupo_id)

    Regras:
      - vínculo com grupo_id: o gestor recebe somente aquele(s) grupo(s);
      - vínculo só com departamento_id: o gestor recebe todos os grupos daquele departamento;
      - um gestor recebe apenas um e-mail com todos os anexos aplicáveis;
      - e-mails duplicados são consolidados para não enviar duas vezes.
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
    except Exception as exc:
        log.warning("get_manager_pdf_bundles: falha ao buscar tenant_users: %s", exc)
        return []

    user_roles = {
        row["user_id"]: row.get("role", "")
        for row in tu_rows
        if row.get("user_id")
    }

    gestor_uids: set[str] = {
        uid for uid, role in user_roles.items()
        if _resolve_tipo(uid, role, prefs) == TIPO_GESTOR
    }
    if not gestor_uids:
        return []

    dep_to_grupos = _fetch_groups_by_department(svc, tenant_id)

    uid_to_dep_ids: dict[str, set[str]] = {}
    uid_dep_to_grp_ids: dict[str, dict[str, set[str]]] = {}

    try:
        rows = (
            svc.table("tenant_user_departamentos")
            .select("user_id,departamento_id,grupo_id")
            .eq("tenant_id", tenant_id)
            .execute()
            .data
        ) or []
    except Exception as exc:
        log.warning("get_manager_pdf_bundles: falha ao buscar tenant_user_departamentos: %s", exc)
        rows = []

    for row in rows:
        uid = row.get("user_id")
        dep_id = row.get("departamento_id")
        grp_id = row.get("grupo_id")
        if not uid or uid not in gestor_uids or not dep_id:
            continue

        uid_to_dep_ids.setdefault(uid, set()).add(dep_id)
        if grp_id:
            uid_dep_to_grp_ids.setdefault(uid, {}).setdefault(dep_id, set()).add(grp_id)

    if not uid_to_dep_ids:
        return []

    all_dep_ids = sorted({d for deps in uid_to_dep_ids.values() for d in deps})
    try:
        dep_rows = (
            svc.table("departamentos")
            .select("id,nome")
            .eq("tenant_id", tenant_id)
            .in_("id", all_dep_ids)
            .execute()
            .data
        ) or []
    except Exception as exc:
        log.warning("get_manager_pdf_bundles: falha ao buscar departamentos: %s", exc)
        dep_rows = []

    dep_nome_map = {row["id"]: row.get("nome", "") for row in dep_rows}

    all_uids = set(uid_to_dep_ids.keys())
    profiles = _fetch_profiles(svc, list(all_uids))
    emails = _fetch_user_emails(svc, all_uids)

    # Consolida por e-mail para evitar duplicidade caso existam dois usuários
    # cadastrados com o mesmo endereço.
    email_to_bundle_data: dict[str, dict[str, Any]] = {}

    for uid, dep_ids_set in uid_to_dep_ids.items():
        email = emails.get(uid, "")
        if not email:
            continue

        entry = email_to_bundle_data.setdefault(
            email,
            {
                "user_id": uid,
                "nome": _nome_from(uid, profiles, email),
                "dep_ids": set(),
                "grp_map": {},
            },
        )

        for dep_id in dep_ids_set:
            entry["dep_ids"].add(dep_id)
            explicit = {
                str(g)
                for g in uid_dep_to_grp_ids.get(uid, {}).get(dep_id, set())
                if g
            }
            grupos_dept = {str(g) for g in dep_to_grupos.get(dep_id, []) if g}

            if explicit:
                entry["grp_map"].setdefault(dep_id, set()).update(explicit)
            else:
                # vínculo amplo por departamento
                entry["grp_map"].setdefault(dep_id, set()).update(grupos_dept)

    bundles: list[ManagerPdfBundle] = []
    for email, data in email_to_bundle_data.items():
        dep_ids = sorted(data["dep_ids"])
        grp_map = {
            dep_id: sorted(data["grp_map"].get(dep_id, set()))
            for dep_id in dep_ids
        }

        bundles.append(
            ManagerPdfBundle(
                recipient=Recipient(
                    user_id=data["user_id"],
                    email=email,
                    nome=data["nome"],
                    tipo_relatorio=TIPO_GESTOR,
                ),
                departamento_ids=dep_ids,
                departamento_nomes=[dep_nome_map.get(d, d) for d in dep_ids],
                grupo_ids_por_dept=grp_map,
            )
        )

    return bundles

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
