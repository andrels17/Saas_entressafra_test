"""Roles do sistema como Enum tipado.

Evita strings mágicas espalhadas pelo código e permite
type-checking e autocomplete em todo o projeto.

Uso:
    from src.auth.roles import Role

    if role in Role.ADMIN_ROLES:
        ...
    if role == Role.GESTOR:
        ...
"""
from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    USER       = "user"
    GESTOR     = "gestor"
    ADMIN      = "admin"
    SUPERADMIN = "superadmin"

    # ── Conjuntos pré-calculados (frozenset para O(1) lookup) ─────────────────
    @classmethod
    @property
    def ADMIN_ROLES(cls) -> frozenset[Role]:
        return frozenset({cls.ADMIN, cls.SUPERADMIN})

    @classmethod
    @property
    def MANAGER_ROLES(cls) -> frozenset[Role]:
        """Roles com acesso a painéis de gestão."""
        return frozenset({cls.GESTOR, cls.ADMIN, cls.SUPERADMIN})

    @classmethod
    @property
    def ALL_ROLES(cls) -> frozenset[Role]:
        return frozenset({cls.USER, cls.GESTOR, cls.ADMIN, cls.SUPERADMIN})

    # ── Helpers ───────────────────────────────────────────────────────────────
    @classmethod
    def is_admin(cls, role: str | None) -> bool:
        """Retorna True se o role tem privilégios de admin/superadmin."""
        return (role or "") in cls.ADMIN_ROLES

    @classmethod
    def is_manager(cls, role: str | None) -> bool:
        """Retorna True se o role tem acesso a painéis de gestão."""
        return (role or "") in cls.MANAGER_ROLES

    @classmethod
    def from_str(cls, value: str | None) -> "Role | None":
        """Converte string para Role, retornando None se inválido."""
        try:
            return cls(value or "")
        except ValueError:
            return None
