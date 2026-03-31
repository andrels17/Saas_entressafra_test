"""Definições de papéis com compatibilidade retroativa.

O sistema usa principalmente a string `gestor`, mas ainda existem pontos
legados que usam `manager`. Este módulo normaliza ambos para o mesmo papel
operacional, sem conceder visão irrestrita.
"""
from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    SUPERADMIN = "superadmin"
    ADMIN = "admin"
    SUPERVISOR = "supervisor"
    GESTOR = "gestor"
    MANAGER = "manager"  # alias legado de GESTOR
    EXECUTOR = "executor"
    USER = "user"
    VIEWER = "viewer"

    @classmethod
    def normalize(cls, value: str | None) -> str:
        if value is None:
            return ""
        raw = getattr(value, "value", value)
        raw = str(raw).strip().lower()
        if raw.startswith("role."):
            raw = raw.split(".", 1)[1]
        if raw == cls.MANAGER.value:
            return cls.GESTOR.value
        return raw

    @classmethod
    def from_str(cls, value: str | None) -> "Role":
        norm = cls.normalize(value)
        mapping = {
            cls.SUPERADMIN.value: cls.SUPERADMIN,
            cls.ADMIN.value: cls.ADMIN,
            cls.SUPERVISOR.value: cls.SUPERVISOR,
            cls.GESTOR.value: cls.GESTOR,
            cls.EXECUTOR.value: cls.EXECUTOR,
            cls.USER.value: cls.USER,
            cls.VIEWER.value: cls.VIEWER,
        }
        return mapping.get(norm, cls.VIEWER)

    @classmethod
    def is_admin(cls, value: str | None) -> bool:
        return cls.normalize(value) in {r.value for r in cls.ADMIN_ROLES}

    @classmethod
    def is_manager(cls, value: str | None) -> bool:
        return cls.normalize(value) in {r.value for r in cls.MANAGER_ROLES}

    @classmethod
    def can_manage(cls, value: str | None) -> bool:
        return cls.is_manager(value)

    @classmethod
    def is_user(cls, value: str | None) -> bool:
        return cls.normalize(value) in {r.value for r in cls.USER_ROLES}


Role.ADMIN_ROLES = {Role.SUPERADMIN, Role.ADMIN}
Role.MANAGER_ROLES = {Role.SUPERADMIN, Role.ADMIN, Role.SUPERVISOR, Role.GESTOR}
Role.USER_ROLES = {Role.SUPERADMIN, Role.ADMIN, Role.SUPERVISOR, Role.GESTOR, Role.EXECUTOR, Role.USER}
Role.ALL_ROLES = {Role.SUPERADMIN, Role.ADMIN, Role.SUPERVISOR, Role.GESTOR, Role.EXECUTOR, Role.USER, Role.VIEWER}

ADMIN_ROLES = Role.ADMIN_ROLES
MANAGER_ROLES = Role.MANAGER_ROLES
USER_ROLES = Role.USER_ROLES
ALL_ROLES = Role.ALL_ROLES
