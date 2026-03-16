"""User scope helpers (Departamento/Grupo) with backward compatibility.

Supports two schemas:

1) Legacy table: tenant_user_scope(tenant_id, user_id, departamento_id, grupo_id)
   - single department + optional single group

2) New table: tenant_user_departamentos(tenant_id, user_id, departamento_id, grupo_id)
   - multiple departments, optional per-department group

The app will prefer the new table when it exists.
"""

from __future__ import annotations

from typing import Iterable


def _uniq(seq: Iterable[str | None]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        if not x:
            continue
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def get_user_scope(
        sb,
        tenant_id: str,
        user_id: str | None,
        role: str | None = None):
    """Return (departamento_ids, grupo_ids).

    - Admin/Superadmin => (None, None)
    - Otherwise:
      - If tenant_user_departamentos exists and has rows => lists
      - Else fallback to tenant_user_scope => singletons as lists
    """
    try:
        if (role or "") in ("admin", "superadmin", "supervisor"):
            return None, None
    except Exception:
        pass

    if not (tenant_id and user_id):
        return None, None

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
            return (dept_ids or None), (grp_ids or None)
    except Exception:
        # table missing or RLS denied; fall back
        pass

    # Legacy schema
    try:
        row = (
            sb.table("tenant_user_scope")
            .select("departamento_id,grupo_id")
            .eq("tenant_id", tenant_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
            .data
        ) or []
        if not row:
            return None, None
        dept_id = row[0].get("departamento_id")
        grp_id = row[0].get("grupo_id")
        return ([dept_id] if dept_id else None), ([grp_id] if grp_id else None)
    except Exception:
        return None, None


def get_my_scope(tenant_id: str, sb=None) -> tuple:
    """Fonte única de verdade para o escopo do usuário logado.

    Lê primeiro do session_state (já calculado pelo app.py no boot).
    Se não encontrar, consulta o banco via get_user_scope.

    Parâmetro `sb` é opcional — se não fornecido, cria um cliente fresco.
    """
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

    if not user_id:
        if sb is not None:
            try:
                u = sb.auth.get_user()
                user_id = (
                    getattr(getattr(u, "user", None), "id", None)
                    or getattr(u, "id", None)
                )
            except Exception:
                pass

    if not user_id or not tenant_id:
        return None, None

    if sb is None:
        from src.utils.supabase_helpers import sb_for_user
        sb = sb_for_user()

    return get_user_scope(sb, tenant_id, user_id, role=role)


def apply_scope_to_query(q, dept_field: str, dept_ids: list[str] | None):
    """Apply departamento scope to a supabase query builder."""
    if not dept_ids:
        return q
    if len(dept_ids) == 1:
        return q.eq(dept_field, dept_ids[0])
    return q.in_(dept_field, dept_ids)


def apply_group_scope_to_query(
        q,
        group_field: str,
        group_ids: list[str] | None):
    if not group_ids:
        return q
    if len(group_ids) == 1:
        return q.eq(group_field, group_ids[0])
    return q.in_(group_field, group_ids)
