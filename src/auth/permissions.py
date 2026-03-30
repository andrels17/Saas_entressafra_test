from __future__ import annotations

from src.auth.roles import Role


def _norm_role(role: str | Role | None) -> str:
    if role is None:
        return ""
    # Suporta Enum Role, strings simples ("admin") e representações acidentais
    # como "Role.ADMIN".
    value = getattr(role, "value", role)
    value = str(value).strip().lower()
    if value.startswith("role."):
        value = value.split(".", 1)[1]
    return value


def can_view_all_data(role: str | Role | None) -> bool:
    norm = _norm_role(role)
    return norm in {
        Role.ADMIN.value,
        Role.SUPERVISOR.value,
        Role.SUPERADMIN.value,
        Role.MANAGER.value,
        Role.GESTOR.value,   # alias PT-BR de manager
    }


def has_restricted_data_scope(role: str | Role | None) -> bool:
    return not can_view_all_data(role)


def can_edit_matriz(role: str | Role | None) -> bool:
    norm = _norm_role(role)
    return norm in {Role.ADMIN.value, Role.SUPERVISOR.value, Role.SUPERADMIN.value, Role.GESTOR.value}
