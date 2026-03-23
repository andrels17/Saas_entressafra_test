"""Definições de papéis de acesso com compatibilidade retroativa."""

from __future__ import annotations


class Role:
    ADMIN = "admin"
    MANAGER = "manager"
    USER = "user"
    VIEWER = "viewer"

    # Compatibilidade com código legado que acessa via Role.*
    ADMIN_ROLES = {ADMIN}
    MANAGER_ROLES = {ADMIN, MANAGER}
    USER_ROLES = {ADMIN, MANAGER, USER}
    ALL_ROLES = {ADMIN, MANAGER, USER, VIEWER}

    @classmethod
    def normalize(cls, value: str | None) -> str:
        if not value:
            return ""
        return str(value).strip().lower()

    @classmethod
    def is_admin(cls, value: str | None) -> bool:
        return cls.normalize(value) in cls.ADMIN_ROLES

    @classmethod
    def can_manage(cls, value: str | None) -> bool:
        return cls.normalize(value) in cls.MANAGER_ROLES

    @classmethod
    def is_user(cls, value: str | None) -> bool:
        return cls.normalize(value) in cls.USER_ROLES


# Compatibilidade com imports de módulo
ADMIN_ROLES = Role.ADMIN_ROLES
MANAGER_ROLES = Role.MANAGER_ROLES
USER_ROLES = Role.USER_ROLES
ALL_ROLES = Role.ALL_ROLES
