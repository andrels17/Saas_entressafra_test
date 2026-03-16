"""Estilos, design tokens e componentes visuais centralizados.

Regras:
- status_badge()      — unico ponto para renderizar badges de status (usa st.badge)
- kpi_card()          — card com barra lateral colorida (usa CSS .ea-card)
- page_header()       — header padronizado com slot de acoes opcionais
- status_chip()       — SOMENTE para retrocompat em contextos HTML; nao use em codigo novo
- render_status_chip()— alias de status_badge(); mantido para retrocompat
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import streamlit as st


# ─────────────────────────────────────────────────────────────────────────────
# PAGE NAV CONFIG
# Mapeamento pagina → (icone, grupo, label_curto)
# ─────────────────────────────────────────────────────────────────────────────
NAV_CONFIG = {
    "Início": ("⌂", "core", "Início"),
    "Dashboard": ("▣", "core", "Dashboard"),
    "Painel do Gestor": ("◈", "core", "Gestor"),
    "Auditoria": ("◎", "core", "Auditoria"),
    "Apontamento": ("◉", "core", "Apontamento"),
    "Matriz": ("⊞", "core", "Matriz"),
    "Configuração Guiada": ("⚙", "admin", "Setup"),
    "Admin - Usuários": ("⊹", "admin", "Usuários"),
    "Admin - Departamentos": ("◩", "admin", "Depart."),
    "Admin - Grupos": ("⊕", "admin", "Grupos"),
    "Admin - Equipamentos": ("◫", "admin", "Equipamentos"),
    "Admin - Integridade": ("🧪", "admin", "Integridade"),
    "Admin - Setores & Serviços": ("◧", "admin", "Setores"),
    "Admin - Templates": ("◪", "admin", "Templates"),
    "Admin - Revisões": ("◑", "admin", "Revisões"),
    "Admin - Branding & Relatórios": ("◒", "admin", "Branding"),
}

# Mapeamento de status para (label visivel, cor st.badge)
_STATUS_BADGE: dict[str, tuple[str, str]] = {
    # Tarefas
    "pendente": ("Pendente", "orange"),
    "em_andamento": ("Em andamento", "blue"),
    "concluido": ("Concluido", "green"),
    "travado": ("Travado", "red"),
    "nao_aplica": ("N/A", "gray"),
    # Revisoes
    "ativa": ("Ativa", "green"),
    "fechada": ("Fechada", "gray"),
    "arquivada": ("Arquivada", "orange"),
    # Risco
    "alto": ("Alto", "red"),
    "medio": ("Medio", "orange"),
    "baixo": ("Baixo", "green"),
}

# Cores hex correspondentes (para contextos HTML que nao aceitam st.badge)
_STATUS_HEX: dict[str, str] = {
    "orange": "#D69E2E",
    "blue": "#3182CE",
    "green": "#38A169",
    "red": "#C53030",
    "gray": "#718096",
}


# ─────────────────────────────────────────────────────────────────────────────
# CSS
# ─────────────────────────────────────────────────────────────────────────────

_CSS_DIR = Path(__file__).parent


def _load_css(filename: str) -> str:
    return (_CSS_DIR / filename).read_text(encoding="utf-8")


def inject_global_css() -> None:
    st.markdown(
        f"<style>{
            _load_css('global.css')}</style>",
        unsafe_allow_html=True)


def inject_mobile_css() -> None:
    """CSS para uso em celular (chao de fabrica).

    Objetivos: alvos de toque maiores, menos densidade, sidebar escondida.
    """
    st.markdown(
        f"<style>{
            _load_css('mobile.css')}</style>",
        unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# STATUS BADGE  (ponto unico — use isto em codigo novo)
# ─────────────────────────────────────────────────────────────────────────────

def status_badge(status: str | None, fallback: str = "—") -> None:
    """Renderiza st.badge com a cor correta para qualquer status do sistema.

    Este e o unico lugar onde o mapeamento status → cor deve existir.
    Substitui render_status_chip() e a versao legada de status_chip() em HTML.
    """
    key = (status or "").lower().strip()
    label, color = _STATUS_BADGE.get(key, (status or fallback, "gray"))
    st.badge(label, color=color)


def status_color(status: str | None) -> str:
    """Retorna a string de cor st.badge para uso em contextos que nao aceitam o widget."""
    key = (status or "").lower().strip()
    return _STATUS_BADGE.get(key, ("", "gray"))[1]


# Alias para retrocompat — prefira status_badge() em codigo novo
def render_status_chip(status: str) -> None:  # noqa: D401
    """Alias de status_badge() mantido para retrocompatibilidade."""
    status_badge(status)


def status_chip(status: str) -> str:
    """Retorna HTML de chip para contextos onde st.badge nao e possivel.

    Preferir status_badge() em codigo novo. Este metodo existe somente para
    contextos de markdown/HTML customizado (ex.: colunas de dataframe).
    """
    key = (status or "").lower().strip()
    label, color_key = _STATUS_BADGE.get(key, (status, "gray"))
    hex_color = _STATUS_HEX.get(color_key, "#718096")
    return (
        f'<span style="display:inline-block;padding:2px 9px;border-radius:6px;'
        f'font-size:0.72rem;font-weight:600;'
        f'background:{hex_color}22;color:{hex_color};border:1px solid {hex_color}44">'
        f'{label}</span>'
    )


# ─────────────────────────────────────────────────────────────────────────────
# KPI CARD  (usa CSS .ea-card definido em global.css)
# ─────────────────────────────────────────────────────────────────────────────

_ACCENT_COLORS = {
    "primary": "#FFD100",
    "ok": "#38A169",
    "warn": "#D69E2E",
    "danger": "#C53030",
    "blue": "#3182CE",
    "neutral": "rgba(255,255,255,0.15)",
}


def kpi_card(
        title: str,
        value: str,
        subtitle: str = "",
        accent: str = "primary") -> None:
    """Card KPI com barra lateral colorida (componente HTML via .ea-card CSS).

    Prefira st.metric para dados simples. Use kpi_card quando precisar de
    valor grande com barra de destaque lateral (ex.: paineis executivos).

    accent: "primary" | "ok" | "warn" | "danger" | "blue" | "neutral" | hex string
    """
    color = _ACCENT_COLORS.get(accent, accent)
    st.markdown(
        f"""<div class="ea-card" style="--accent-color:{color}">
          <div class="ea-title">{title}</div>
          <div class="ea-value">{value}</div>
          <div class="ea-sub">{subtitle}</div>
        </div>""",
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# PILL  (wrapper fino sobre st.badge para compatibilidade de chamadas antigas)
# ─────────────────────────────────────────────────────────────────────────────

def pill(text: str, variant: str = "") -> None:
    """Exibe uma pilula colorida usando st.badge nativo (1.42+)."""
    _variant_color = {
        "ok": "green",
        "warn": "orange",
        "danger": "red",
        "info": "blue",
        "neutral": "gray",
        "primary": "violet",
    }
    color = _variant_color.get(variant, "gray")
    st.badge(text, color=color)


# ─────────────────────────────────────────────────────────────────────────────
# PAGE HEADER  (com slot de acoes no lado direito)
# ─────────────────────────────────────────────────────────────────────────────

def page_header(
    *args,
    actions: Callable[[], None] | None = None,
    **kwargs,
) -> None:
    """Header padronizado de pagina com slot de acoes opcional.

    Formatos posicionais suportados (retrocompativel):
      page_header(title)
      page_header(title, subtitle)
      page_header(icon, title, subtitle)

    Kwargs:
      icon=...       icone de texto (ex.: "▣")
      title=...      titulo principal
      subtitle=...   descricao curta
      actions=...    callable sem argumentos que renderiza widgets no lado direito
                     (ex.: botoes Exportar, Atualizar)

    Exemplo com acoes:
        def _acoes():
            if st.button("Exportar CSV", key="h_export"):
                ...

        page_header("▣", "Dashboard", "Visao geral", actions=_acoes)
    """
    icon = kwargs.pop("icon", "")
    title = kwargs.pop("title", "")
    subtitle = kwargs.pop("subtitle", "")

    if kwargs:
        raise TypeError(
            f"page_header() recebeu kwargs inesperados: {
                ', '.join(
                    kwargs.keys())}")

    if args:
        if len(args) == 1:
            title = args[0]
        elif len(args) == 2:
            title, subtitle = args
        elif len(args) == 3:
            icon, title, subtitle = args
        else:
            raise TypeError(
                "page_header() aceita 1, 2 ou 3 argumentos posicionais")

    icon = str(icon or "").strip()
    title = str(title or "").strip()
    subtitle = str(subtitle or "").strip()

    if actions is not None:
        left, right = st.columns([0.76, 0.24], gap="small")
    else:
        left = st.columns(1)[0]
        right = None

    with left:
        heading = f"### {icon} {title}" if icon else f"### {title}"
        st.markdown(heading)
        if subtitle:
            st.caption(subtitle)

    if right is not None and actions is not None:
        with right:
            actions()
