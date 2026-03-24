from __future__ import annotations

import pandas as pd


def calcular_kpis(df: pd.DataFrame) -> dict[str, int]:
    """Calcula KPIs básicos a partir de um DataFrame de tarefas.

    Retorna zeros se o DataFrame for None, vazio ou se as colunas
    esperadas não existirem — nunca lança exceção para o caller.
    """
    if df is None or df.empty:
        return {"total": 0, "concluidos": 0, "atrasados": 0, "equipamentos": 0}

    total = len(df)
    concluidos = int((df["status"] == "concluido").sum()) if "status" in df.columns else 0
    atrasados = int((df["status"] == "atrasado").sum()) if "status" in df.columns else 0
    equipamentos = df["equipamento"].nunique() if "equipamento" in df.columns else 0

    return {
        "total": total,
        "concluidos": concluidos,
        "atrasados": atrasados,
        "equipamentos": equipamentos,
    }
