from __future__ import annotations

from src.auth.roles import Role


def _norm_role(role: str | Role | None) -> str:
    return Role.normalize(role)


def can_view_all_data(role: str | Role | None) -> bool:
    norm = _norm_role(role)
    # Gestor NÃO é irrestrito; respeita vínculo por departamento/grupo.
    return norm in {
        Role.ADMIN.value,
        Role.SUPERVISOR.value,
        Role.SUPERADMIN.value,
    }


def has_restricted_data_scope(role: str | Role | None) -> bool:
    return not can_view_all_data(role)


def can_edit_matriz(role: str | Role | None) -> bool:
    norm = _norm_role(role)
    return norm in {Role.ADMIN.value, Role.SUPERVISOR.value, Role.SUPERADMIN.value}
