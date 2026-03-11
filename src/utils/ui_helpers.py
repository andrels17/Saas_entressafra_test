"""Helpers de UI reutilizáveis — centralizados para consistência visual.

Uso:
    from src.utils.ui_helpers import status_badge, df_to_xlsx, revisao_badge

    status_badge("travado")           # renderiza st.badge com cor correta
    xlsx = df_to_xlsx(df)             # retorna bytes prontos para st.download_button
"""
from __future__ import annotations

import io
from typing import Any

import pandas as pd
import streamlit as st


# ── Mapa de status → (label, cor) ────────────────────────────────────────────
_STATUS_CONFIG: dict[str, tuple[str, str]] = {
    # Tarefas
    "pendente":     ("Pendente",     "orange"),
    "em_andamento": ("Em andamento", "blue"),
    "concluido":    ("Concluído",    "green"),
    "travado":      ("Travado",      "red"),
    "nao_aplica":   ("Não aplica",   "gray"),
    # Revisões
    "ativa":        ("Ativa",        "green"),
    "fechada":      ("Fechada",      "gray"),
    "arquivada":    ("Arquivada",    "orange"),
    # Risco
    "alto":         ("Alto",         "red"),
    "medio":        ("Médio",        "orange"),
    "baixo":        ("Baixo",        "green"),
}


def status_badge(status: str | None, fallback: str = "—") -> None:
    """Renderiza st.badge com cor correta para qualquer status do sistema.

    Centraliza a lógica que estava duplicada em home_overview, dashboard e gestor.
    """
    key = (status or "").lower().strip()
    label, color = _STATUS_CONFIG.get(key, (status or fallback, "gray"))
    st.badge(label, color=color)


def status_color(status: str | None) -> str:
    """Retorna apenas a string de cor para uso em contextos não-badge."""
    key = (status or "").lower().strip()
    return _STATUS_CONFIG.get(key, ("", "gray"))[1]


def df_to_xlsx(df: pd.DataFrame, sheet_name: str = "Dados") -> bytes:
    """Converte DataFrame para bytes XLSX prontos para st.download_button.

    Aplica auto-ajuste de largura de coluna e freeze da primeira linha.
    Requer openpyxl (já dependência transitiva do pandas).

    Exemplo:
        st.download_button("⬇ XLSX", df_to_xlsx(df), "relatorio.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    """
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
        ws = writer.sheets[sheet_name]

        # Auto-ajuste de largura + freeze header
        for col_cells in ws.columns:
            max_len = max(
                (len(str(cell.value)) if cell.value is not None else 0)
                for cell in col_cells
            )
            ws.column_dimensions[col_cells[0].column_letter].width = min(max_len + 4, 60)
        ws.freeze_panes = "A2"

    return buf.getvalue()


def mobile_columns(n_desktop: int, n_mobile: int = 1) -> list[Any]:
    """Retorna st.columns adaptado ao modo mobile/desktop.

    Uso:
        cols = mobile_columns(6)   # 6 no desktop, 2 no mobile
        cols = mobile_columns(4, 2)  # 4 no desktop, 2 no mobile
    """
    from src.utils.mobile import is_mobile
    n = n_mobile if is_mobile() else n_desktop
    return st.columns(n)
