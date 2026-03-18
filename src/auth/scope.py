"""User scope helpers (Departamento/Grupo) with backward compatibility.

Conventions:
- None => unrestricted scope (admin/supervisor/superadmin)
- []   => restricted user with no vínculo (deny all)
"""

from __future__ import annotations

from typing import Iterable

from src.auth.permissions import can_view_all_data


def _uniq(seq: Iterable[str | None]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        if not x or x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def empty_scope() -> tuple[list[str], list[str]]:
    return [], []


def has_any_scope(dep_ids: list[str] | None, grp_ids: list[str] | None) -> bool:
    if dep_ids is None or grp_ids is None:
        return True
    return bool(dep_ids or grp_ids)


def _expand_groups_from_departments(sb, tenant_id: str, dept_ids: list[str]) -> list[str]:
    if not dept_ids:
        return []
    try:
        rows = (
            sb.table("equip_grupos")
            .select("id,departamento_id")
            .eq("tenant_id", tenant_id)
            .eq("ativo", True)
            .in_("departamento_id", dept_ids)
            .execute()
            .data
        ) or []
        return _uniq([r.get("id") for r in rows])
    except Exception:
        return []


def get_user_scope(sb, tenant_id: str, user_id: str | None, role: str | None = None):
    """Return (departamento_ids, grupo_ids).

    - Admin/Supervisor/Superadmin => (None, None)
    - Otherwise => lists, empty when the user has no vínculo
    """
    if can_view_all_data(role):
        return None, None

    if not (tenant_id and user_id):
        return empty_scope()

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
                grp_ids = _expand_groups_from_departments(sb, tenant_id, dept_ids)
            return dept_ids, grp_ids
    except Exception:
        pass

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
                grp_ids = _expand_groups_from_departments(sb, tenant_id, dept_ids)
            return dept_ids, grp_ids
    except Exception:
        pass

    return empty_scope()


def get_my_scope(tenant_id: str, sb=None) -> tuple:
    import streamlit as st

    role = st.session_state.get("current_role") or ""

    dep_ids = st.session_state.get("scope_departamento_ids")
    grp_ids = st.session_state.get("scope_grupo_ids")
    if dep_ids is None and grp_ids is None and can_view_all_data(role):
        return None, None

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

    if not tenant_id:
        return empty_scope() if not can_view_all_data(role) else (None, None)

    if sb is None:
        from src.utils.supabase_helpers import sb_for_user
        sb = sb_for_user()

    # Recalcula sempre para evitar scope stale após alterar vínculos do usuário.
    dep_ids, grp_ids = get_user_scope(sb, tenant_id, user_id, role=role)
    st.session_state["scope_departamento_ids"] = dep_ids
    st.session_state["scope_grupo_ids"] = grp_ids
    return dep_ids, grp_ids


def apply_scope_to_query(q, dept_field: str, dept_ids: list[str] | None):
    if dept_ids is None:
        return q
    if not dept_ids:
        return q.eq(dept_field, "__no_scope__")
    if len(dept_ids) == 1:
        return q.eq(dept_field, dept_ids[0])
    return q.in_(dept_field, dept_ids)


def apply_group_scope_to_query(q, group_field: str, group_ids: list[str] | None):
    if group_ids is None:
        return q
    if not group_ids:
        return q.eq(group_field, "__no_scope__")
    if len(group_ids) == 1:
        return q.eq(group_field, group_ids[0])
    return q.in_(group_field, group_ids)
