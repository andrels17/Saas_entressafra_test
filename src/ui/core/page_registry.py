"""Registro central de páginas da aplicação.

Elimina strings mágicas de nomes de página espalhadas em app.py,
NAV_CONFIG, session_state e código de roteamento.

Uso:
    from src.ui.core.page_registry import PageKey, PAGES, get_pages_for_role

    pages = get_pages_for_role(role)
    st.session_state["__current_page"] = PageKey.DASHBOARD
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from src.auth.roles import Role


class PageKey(str, Enum):
    """Chaves únicas para cada página da aplicação."""
    INICIO = "Início"
    DASHBOARD = "Dashboard"
    MATRIZ = "Matriz"
    NOTIFICACOES = "Notificações"
    PAINEL_GESTOR = "Painel do Gestor"
    AUDITORIA = "Auditoria"
    APONTAMENTO = "Apontamento"
    CONFIG_GUIADA = "Configuração Guiada"
    ADM_USUARIOS = "Admin - Usuários"
    ADM_DEPARTAMENTOS = "Admin - Departamentos"
    ADM_GRUPOS = "Admin - Grupos"
    ADM_EQUIPAMENTOS = "Admin - Equipamentos"
    ADM_INTEGRIDADE = "Admin - Integridade"
    ADM_SETORES_SERVICOS = "Admin - Setores & Serviços"
    ADM_TEMPLATES = "Admin - Templates"
    ADM_REVISOES = "Admin - Revisões"
    ADM_BRANDING = "Admin - Branding & Relatórios"


@dataclass(frozen=True)
class PageConfig:
    """Configuração de uma página: metadados + controle de acesso."""
    key: PageKey
    icon: str
    label: str          # label curto para sidebar compacta
    group: str          # "core" | "admin"
    roles: frozenset[Role] = field(default_factory=frozenset)
    # render_fn é injetado em runtime pelo app.py para evitar import circular


# ── Tabela de configuração de todas as páginas ──────────────────────────
#    (ícone, label_curto, grupo, roles_requeridas)
#
#    Grupos suportados:
#      "core"   — aparece no menu principal
#      "admin"  — aparece no menu de administração (colapsável)
#      "detail" — NÃO aparece no menu (rota de detalhe, acessível via __nav_to)
# Equivalente a st.Page(visibility="hidden") do Streamlit 1.44+ (#5)
_PAGE_DEFS: list[tuple[PageKey, str, str, str, frozenset[Role]]] = [
    # key                          icon   label         group    roles (vazio
    # = todos logados)
    (PageKey.INICIO, "⌂", "Início", "core", frozenset()),
    (PageKey.DASHBOARD, "▣", "Dashboard", "core", frozenset()),
    (PageKey.MATRIZ, "⊞", "Matriz", "core", frozenset()),
    (PageKey.NOTIFICACOES, "🔔", "Notificações", "core", frozenset()),
    (PageKey.PAINEL_GESTOR, "◈", "Gestor", "core", Role.MANAGER_ROLES),
    (PageKey.AUDITORIA, "◎", "Auditoria", "core", Role.MANAGER_ROLES),
    (PageKey.APONTAMENTO, "◉", "Apontamento", "core", Role.ADMIN_ROLES),
    (PageKey.CONFIG_GUIADA, "⚙", "Setup", "admin", Role.ADMIN_ROLES),
    (PageKey.ADM_USUARIOS, "⊹", "Usuários", "admin", Role.ADMIN_ROLES),
    (PageKey.ADM_DEPARTAMENTOS, "◩", "Depart.", "admin", Role.ADMIN_ROLES),
    (PageKey.ADM_GRUPOS, "⊕", "Grupos", "admin", Role.ADMIN_ROLES),
    (PageKey.ADM_EQUIPAMENTOS, "◫", "Equipamentos", "admin", Role.ADMIN_ROLES),
    (PageKey.ADM_INTEGRIDADE, "🧪", "Integridade", "admin", Role.ADMIN_ROLES),
    (PageKey.ADM_SETORES_SERVICOS, "◧", "Setores", "admin", Role.ADMIN_ROLES),
    (PageKey.ADM_TEMPLATES, "◪", "Templates", "admin", Role.ADMIN_ROLES),
    (PageKey.ADM_REVISOES, "◑", "Revisões", "admin", Role.ADMIN_ROLES),
    (PageKey.ADM_BRANDING, "◒", "Branding", "admin", Role.ADMIN_ROLES),
]

PAGES: dict[PageKey, PageConfig] = {
    key: PageConfig(key=key, icon=icon, label=label, group=group, roles=roles)
    for key, icon, label, group, roles in _PAGE_DEFS
}

# Compatibilidade retroativa com NAV_CONFIG (dict[str, tuple])
NAV_CONFIG: dict[str, tuple[str, str, str]] = {
    cfg.key.value: (cfg.icon, cfg.group, cfg.label)
    for cfg in PAGES.values()
}


def get_pages_for_role(role: str | None) -> list[str]:
    """Retorna TODAS as páginas acessíveis (menu + detail) para o role informado."""
    r = role or ""
    result: list[str] = []
    for cfg in PAGES.values():
        if not cfg.roles or r in cfg.roles:
            result.append(cfg.key.value)
    return result


def get_menu_pages(role: str | None) -> list[str]:
    """Retorna apenas páginas que devem aparecer no menu (group != 'detail').

    Equivalente a st.Page(visibility='hidden') do Streamlit 1.44+ (#5):
    páginas com group='detail' existem no roteamento mas ficam fora do menu.
    """
    r = role or ""
    result: list[str] = []
    for cfg in PAGES.values():
        if cfg.group == "detail":
            continue
        if not cfg.roles or r in cfg.roles:
            result.append(cfg.key.value)
    return result


def get_detail_pages(role: str | None) -> list[str]:
    """Retorna apenas páginas de detalhe (group='detail') para o role informado."""
    r = role or ""
    return [
        cfg.key.value
        for cfg in PAGES.values()
        if cfg.group == "detail" and (not cfg.roles or r in cfg.roles)
    ]
