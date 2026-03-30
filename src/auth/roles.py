"""Definições de papéis de acesso com compatibilidade retroativa.

Compatível com usos como:
- Role.ADMIN.value
- Role.SUPERVISOR.value
- Role.SUPERADMIN.value
- Role.MANAGER_ROLES
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    SUPERADMIN = "superadmin"
    ADMIN = "admin"
    SUPERVISOR = "supervisor"
    MANAGER = "manager"
    GESTOR = "gestor"      # alias PT-BR de MANAGER usado no banco
    EXECUTOR = "executor"  # alias PT-BR de USER usado no banco
    USER = "user"
    VIEWER = "viewer"

    # Compatibilidade com código legado
    @classmethod
    def normalize(cls, value: str | None) -> str:
        if value is None:
            return ""
        if isinstance(value, cls):
            return value.value
        return str(value).strip().lower()

    @classmethod
    def is_admin(cls, value: str | None) -> bool:
        return cls.normalize(value) in {r.value for r in cls.ADMIN_ROLES}

    @classmethod
    def can_manage(cls, value: str | None) -> bool:
        return cls.normalize(value) in {r.value for r in cls.MANAGER_ROLES}

    @classmethod
    def is_user(cls, value: str | None) -> bool:
        return cls.normalize(value) in {r.value for r in cls.USER_ROLES}


# Coleções legadas acessadas como Role.* e por import de módulo
Role.ADMIN_ROLES = {Role.SUPERADMIN, Role.ADMIN}
Role.MANAGER_ROLES = {Role.SUPERADMIN, Role.ADMIN, Role.SUPERVISOR, Role.MANAGER, Role.GESTOR}
Role.USER_ROLES = {Role.SUPERADMIN, Role.ADMIN, Role.SUPERVISOR, Role.MANAGER, Role.GESTOR, Role.USER, Role.EXECUTOR}
Role.ALL_ROLES = {Role.SUPERADMIN, Role.ADMIN, Role.SUPERVISOR, Role.MANAGER, Role.GESTOR, Role.USER, Role.EXECUTOR, Role.VIEWER}

ADMIN_ROLES = Role.ADMIN_ROLES
MANAGER_ROLES = Role.MANAGER_ROLES
USER_ROLES = Role.USER_ROLES
ALL_ROLES = Role.ALL_ROLES
