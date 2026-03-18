from __future__ import annotations

from src.auth.roles import Role


def can_view_all_data(role: str | None) -> bool:
    return (role or "") in {Role.ADMIN, Role.SUPERVISOR, Role.SUPERADMIN}


def has_restricted_data_scope(role: str | None) -> bool:
    return not can_view_all_data(role)


def can_edit_matriz(role: str | None) -> bool:
    return (role or "") in {Role.ADMIN, Role.SUPERVISOR, Role.SUPERADMIN}
