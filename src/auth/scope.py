"""Helpers de escopo operacional (departamentos/grupos).

Convencao:
- (None, None) => acesso irrestrito
- ([], [])     => usuario restrito sem vinculo
"""

from __future__ import annotations

from typing import Iterable

from src.auth.permissions import can_view_all_data


def _to_id(value) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _uniq(seq: Iterable[object]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in seq:
        value = _to_id(raw)
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def empty_scope() -> tuple[list[str], list[str]]:
    return [], []


def _load_scope_rows(sb, table: str, tenant_id: str, user_id: str) -> list[dict]:
    try:
        return (
            sb.table(table)
            .select("departamento_id,grupo_id")
            .eq("tenant_id", tenant_id)
            .eq("user_id", user_id)
            .execute()
            .data
        ) or []
    except Exception:
        return []


def _derive_groups_from_departments(sb, tenant_id: str, dept_ids: list[str]) -> list[str]:
    if not dept_ids:
        return []
    try:
        rows = (
            sb.table("equip_grupos")
            .select("id")
            .eq("tenant_id", tenant_id)
            .in_("departamento_id", dept_ids)
            .execute()
            .data
        ) or []
        return _uniq(r.get("id") for r in rows)
    except Exception:
        return []


def _derive_departments_from_groups(sb, tenant_id: str, grp_ids: list[str]) -> list[str]:
    if not grp_ids:
        return []
    try:
        rows = (
            sb.table("equip_grupos")
            .select("departamento_id")
            .eq("tenant_id", tenant_id)
            .in_("id", grp_ids)
            .execute()
            .data
        ) or []
        return _uniq(r.get("departamento_id") for r in rows)
    except Exception:
        return []


def get_user_scope(sb, tenant_id: str, user_id: str | None, role: str | None = None):
    if can_view_all_data(role):
        return None, None

    tenant_id = _to_id(tenant_id)
    user_id = _to_id(user_id)
    if not tenant_id or not user_id:
        return empty_scope()

    rows: list[dict] = []
    rows.extend(_load_scope_rows(sb, "tenant_user_departamentos", tenant_id, user_id))
    rows.extend(_load_scope_rows(sb, "tenant_user_scope", tenant_id, user_id))

    dept_ids = _uniq(r.get("departamento_id") for r in rows)
    grp_ids = _uniq(r.get("grupo_id") for r in rows)

    if dept_ids and not grp_ids:
        grp_ids = _derive_groups_from_departments(sb, tenant_id, dept_ids)
    elif grp_ids and not dept_ids:
        dept_ids = _derive_departments_from_groups(sb, tenant_id, grp_ids)

    if not dept_ids and not grp_ids:
        return empty_scope()
    return dept_ids, grp_ids


def get_my_scope(tenant_id: str, sb=None) -> tuple[list[str] | None, list[str] | None]:
    import streamlit as st

    role = st.session_state.get("current_role") or ""
    if can_view_all_data(role):
        st.session_state["scope_departamento_ids"] = None
        st.session_state["scope_grupo_ids"] = None
        return None, None

    user_id = (
        st.session_state.get("sb_user_id")
        or st.session_state.get("user_id")
        or st.session_state.get("auth_user_id")
    )

    if sb is None:
        from src.utils.supabase_helpers import sb_for_user
        sb = sb_for_user()

    if not user_id and sb is not None:
        try:
            u = sb.auth.get_user()
            user_id = getattr(getattr(u, "user", None), "id", None) or getattr(u, "id", None)
        except Exception:
            pass  # ignorado — operação opcional

    dept_ids, grp_ids = get_user_scope(sb, tenant_id, user_id, role=role)
    st.session_state["scope_departamento_ids"] = dept_ids
    st.session_state["scope_grupo_ids"] = grp_ids
    return dept_ids, grp_ids


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
