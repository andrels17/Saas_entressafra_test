"""User scope helpers (Departamento/Grupo) with backward compatibility.

Supports two schemas:

1) Legacy table: tenant_user_scope(tenant_id, user_id, departamento_id, grupo_id)
2) New table: tenant_user_departamentos(tenant_id, user_id, departamento_id, grupo_id)

Semântica importante:
- (None, None) => acesso irrestrito
- ([], []) ou listas vazias => usuário restrito sem vínculo operacional
"""

from __future__ import annotations

from typing import Iterable


def _uniq(seq: Iterable[str | None]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        if not x or x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _expand_group_ids_from_departments(sb, tenant_id: str, dept_ids: list[str] | None) -> list[str]:
    if not dept_ids:
        return []
    try:
        q = sb.table("equip_grupos").select("id,departamento_id").eq("tenant_id", tenant_id).eq("ativo", True)
        q = q.eq("departamento_id", dept_ids[0]) if len(dept_ids) == 1 else q.in_("departamento_id", dept_ids)
        rows = q.execute().data or []
        return _uniq([r.get("id") for r in rows])
    except Exception:
        return []


def get_user_scope(sb, tenant_id: str, user_id: str | None, role: str | None = None):
    """Return (departamento_ids, grupo_ids).

    - Admin/Superadmin/Supervisor => (None, None)
    - Usuário restrito sem vínculos => ([], [])
    - Usuário com departamentos e sem grupos explícitos => expande grupos pelos departamentos
    """
    try:
        if (role or "") in ("admin", "superadmin", "supervisor"):
            return None, None
    except Exception:
        pass

    if not (tenant_id and user_id):
        return [], []

    # Prefer new schema: multiple departments
    try:
        rows = (
            sb.table("tenant_user_departamentos")
            .select("departamento_id,grupo_id")
            .eq("tenant_id", tenant_id)
            .eq("user_id", user_id)
            .execute()
            .data
        ) or []
        if rows:
            dept_ids = _uniq([r.get("departamento_id") for r in rows])
            grp_ids = _uniq([r.get("grupo_id") for r in rows])
            if dept_ids and not grp_ids:
                grp_ids = _expand_group_ids_from_departments(sb, tenant_id, dept_ids)
            return dept_ids, grp_ids
    except Exception:
        pass

    # Legacy schema
    try:
        rows = (
            sb.table("tenant_user_scope")
            .select("departamento_id,grupo_id")
            .eq("tenant_id", tenant_id)
            .eq("user_id", user_id)
            .execute()
            .data
        ) or []
        if rows:
            dept_ids = _uniq([r.get("departamento_id") for r in rows])
            grp_ids = _uniq([r.get("grupo_id") for r in rows])
            if dept_ids and not grp_ids:
                grp_ids = _expand_group_ids_from_departments(sb, tenant_id, dept_ids)
            return dept_ids, grp_ids
        return [], []
    except Exception:
        return [], []


def get_my_scope(tenant_id: str, sb=None) -> tuple:
    """Fonte única de verdade para o escopo do usuário logado."""
    import streamlit as st

    dep_ids = st.session_state.get("scope_departamento_ids")
    grp_ids = st.session_state.get("scope_grupo_ids")
    if dep_ids is not None or grp_ids is not None:
        return dep_ids, grp_ids

    role = st.session_state.get("current_role") or ""
    user_id = (
        st.session_state.get("sb_user_id")
        or st.session_state.get("user_id")
        or st.session_state.get("auth_user_id")
    )

    if not user_id and sb is not None:
        try:
            u = sb.auth.get_user()
            user_id = getattr(getattr(u, "user", None), "id", None) or getattr(u, "id", None)
        except Exception:
            pass

    if not user_id or not tenant_id:
        return [], [] if role not in ("admin", "superadmin", "supervisor") else (None, None)

    if sb is None:
        from src.utils.supabase_helpers import sb_for_user
        sb = sb_for_user()

    return get_user_scope(sb, tenant_id, user_id, role=role)


def apply_scope_to_query(q, dept_field: str, dept_ids: list[str] | None):
    if dept_ids is None:
        return q
    if not dept_ids:
        return q.in_(dept_field, ["__no_scope__"])
    if len(dept_ids) == 1:
        return q.eq(dept_field, dept_ids[0])
    return q.in_(dept_field, dept_ids)


def apply_group_scope_to_query(q, group_field: str, group_ids: list[str] | None):
    if group_ids is None:
        return q
    if not group_ids:
        return q.in_(group_field, ["__no_scope__"])
    if len(group_ids) == 1:
        return q.eq(group_field, group_ids[0])
    return q.in_(group_field, group_ids)
