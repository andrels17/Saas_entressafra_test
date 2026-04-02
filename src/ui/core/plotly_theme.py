"""Tema escuro centralizado para todos os gráficos Plotly do dashboard.

Uso:
    from src.ui.core.plotly_theme import apply_dark_theme, DARK_LAYOUT

    # Opção 1 — aplicar sobre um fig existente
    fig = px.bar(...)
    apply_dark_theme(fig)

    # Opção 2 — passar como **kwargs no update_layout
    fig.update_layout(**DARK_LAYOUT)
"""
from __future__ import annotations

import plotly.graph_objects as go

# ── Paleta de cores do projeto ───────────────────────────────────────────────
BG_PAGE   = "#06080B"   # paper_bgcolor  — fundo externo do gráfico
BG_PLOT   = "#0C111A"   # plot_bgcolor   — área interna do gráfico
TEXT      = "#F5F5F5"   # texto principal (títulos, labels, ticks)
MUTED     = "#8A9BAE"   # texto secundário (legendas, títulos de eixo)
GRID      = "rgba(255,255,255,0.07)"  # linhas de grade sutis
ACCENT    = "#FFD100"   # cor de destaque do projeto


def _axis_style(title: str = "") -> dict:
    """Retorna configuração padrão para um eixo com tema escuro."""
    return dict(
        title=title,
        title_font=dict(color=MUTED, size=11),
        tickfont=dict(color=MUTED, size=10),
        gridcolor=GRID,
        zerolinecolor=GRID,
        linecolor=GRID,
    )


# Dict pronto para uso em fig.update_layout(**DARK_LAYOUT)
DARK_LAYOUT: dict = dict(
    paper_bgcolor=BG_PAGE,
    plot_bgcolor=BG_PLOT,
    font=dict(color=TEXT, family="DM Sans, system-ui, sans-serif"),
    legend=dict(
        font=dict(color=MUTED, size=11),
        bgcolor="rgba(0,0,0,0)",
        bordercolor="rgba(255,255,255,0.08)",
    ),
    xaxis=_axis_style(),
    yaxis=_axis_style(),
    margin=dict(l=6, r=10, t=12, b=10),
    hoverlabel=dict(
        bgcolor="#1A2332",
        font_color=TEXT,
        font_size=12,
        bordercolor="rgba(255,255,255,0.12)",
    ),
)


def apply_dark_theme(
    fig: go.Figure,
    height: int | None = None,
    xaxis_title: str = "",
    yaxis_title: str = "",
) -> go.Figure:
    """Aplica o tema escuro padrão a qualquer figura Plotly.

    Args:
        fig: A figura Plotly a ser estilizada (modificada in-place).
        height: Altura em pixels (opcional).
        xaxis_title: Título do eixo X (opcional).
        yaxis_title: Título do eixo Y (opcional).

    Returns:
        A mesma figura, após aplicar os estilos.
    """
    layout = dict(DARK_LAYOUT)
    layout["xaxis"] = {**_axis_style(xaxis_title)}
    layout["yaxis"] = {**_axis_style(yaxis_title)}
    if height:
        layout["height"] = height
    fig.update_layout(**layout)
    return fig
