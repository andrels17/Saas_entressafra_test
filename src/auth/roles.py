"""Definições de papéis de acesso com compatibilidade retroativa.

Compatível com usos como:
- Role.ADMIN.value
- Role.GESTOR.value
- Role.MANAGER (alias legado de gestor)
- Role.MANAGER_ROLES / Role.ADMIN_ROLES / Role.SUPERVISOR_ROLES
- Role.from_str(...)
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    SUPERADMIN = "superadmin"
    ADMIN = "admin"
    SUPERVISOR = "supervisor"
    GESTOR = "gestor"
    MANAGER = "gestor"  # alias legado
    USER = "user"
    VIEWER = "viewer"
    EXECUTOR = "executor"

    @classmethod
    def normalize(cls, value: str | None) -> str:
        if value is None:
            return ""
        if isinstance(value, cls):
            value = value.value
        value = str(value).strip().lower()
        aliases = {
            "manager": cls.GESTOR.value,
            "gestor": cls.GESTOR.value,
            "executor": cls.USER.value,
            "operador": cls.USER.value,
        }
        return aliases.get(value, value)

    @classmethod
    def from_str(cls, value: str | None):
        norm = cls.normalize(value)
        for role in cls:
            if role.value == norm:
                return role
        return None

    @classmethod
    def is_admin(cls, value: str | None) -> bool:
        return cls.normalize(value) in cls.ADMIN_ROLES

    @classmethod
    def is_manager(cls, value: str | None) -> bool:
        return cls.normalize(value) in cls.MANAGER_ROLES

    @classmethod
    def can_manage(cls, value: str | None) -> bool:
        return cls.is_manager(value)

    @classmethod
    def is_user(cls, value: str | None) -> bool:
        return cls.normalize(value) in cls.USER_ROLES


Role.ADMIN_ROLES = frozenset({Role.SUPERADMIN.value, Role.ADMIN.value})
Role.SUPERVISOR_ROLES = frozenset({Role.SUPERADMIN.value, Role.ADMIN.value, Role.SUPERVISOR.value})
Role.MANAGER_ROLES = frozenset({Role.SUPERADMIN.value, Role.ADMIN.value, Role.SUPERVISOR.value, Role.GESTOR.value})
Role.USER_ROLES = frozenset({Role.SUPERADMIN.value, Role.ADMIN.value, Role.SUPERVISOR.value, Role.GESTOR.value, Role.USER.value})
Role.ALL_ROLES = frozenset({Role.SUPERADMIN.value, Role.ADMIN.value, Role.SUPERVISOR.value, Role.GESTOR.value, Role.USER.value, Role.VIEWER.value, Role.EXECUTOR.value})

ADMIN_ROLES = Role.ADMIN_ROLES
SUPERVISOR_ROLES = Role.SUPERVISOR_ROLES
MANAGER_ROLES = Role.MANAGER_ROLES
USER_ROLES = Role.USER_ROLES
ALL_ROLES = Role.ALL_ROLES
