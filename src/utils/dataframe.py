"""Helpers genéricos para normalização de DataFrames.

Elimina o padrão repetitivo de rename + fill defaults + coerce types
que aparecia em _normalize_heatmap_df, _normalize_criticidade_df e
_normalize_timeline_df dentro de dashboard.py.

Uso:
    from src.utils.dataframe import normalize_df, pct_series

    df = normalize_df(
        df,
        rename_map={"grupo_nome": "grupo"},
        defaults={"grupo": "—", "calor_score": 0},
        numeric_cols=["calor_score"],
        fillna={"grupo": "—"},
    )
"""
from __future__ import annotations

from typing import Any

import pandas as pd


def normalize_df(
    df: pd.DataFrame,
    rename_map: dict[str, str] | None = None,
    defaults: dict[str, Any] | None = None,
    numeric_cols: list[str] | None = None,
    fillna: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Normaliza um DataFrame de forma idempotente.

    Parâmetros
    ----------
    df:
        DataFrame de entrada. Nunca modificado in-place.
    rename_map:
        {nome_antigo: nome_novo}. Só renomeia se a coluna antiga existe
        e a nova ainda não existe.
    defaults:
        {coluna: valor_padrão}. Adiciona a coluna se ela não existir.
    numeric_cols:
        Lista de colunas a converter com pd.to_numeric(errors='coerce').
        Aplica fillna(0) automaticamente após a conversão.
    fillna:
        {coluna: valor}. Preenche NaN após renomear e aplicar defaults.
    """
    if df is None or not isinstance(df, pd.DataFrame):
        return pd.DataFrame()

    out = df.copy()

    if rename_map:
        for old, new in rename_map.items():
            if old in out.columns and new not in out.columns:
                out = out.rename(columns={old: new})

    if defaults:
        for col, val in defaults.items():
            if col not in out.columns:
                out[col] = val

    if numeric_cols:
        for col in numeric_cols:
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)

    if fillna:
        for col, val in fillna.items():
            if col in out.columns:
                out[col] = out[col].fillna(val)

    return out


def safe_numeric(series: pd.Series, fill: float = 0.0) -> pd.Series:
    """Converte uma Serie para numérico de forma segura."""
    return pd.to_numeric(series, errors="coerce").fillna(fill)


def pct_series(done: pd.Series, total: pd.Series,
               scale: float = 100.0) -> pd.Series:
    """Calcula percentual vetorizado, evitando divisão por zero."""
    total_safe = total.clip(lower=1)
    return (done / total_safe * scale).round(1).clip(0, scale)


def compact_top(
        df: pd.DataFrame,
        cols: list[str],
        n: int = 12) -> pd.DataFrame:
    """Retorna as primeiras `n` linhas apenas com as colunas existentes."""
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    keep = [c for c in cols if c in df.columns]
    if not keep:
        return pd.DataFrame()
    return df[keep].head(n).reset_index(drop=True)


def fmt_int(v) -> str:
    """Formata inteiro com separador de milhar (ex.: 12345 → '12,345').

    Retorna str(v) se a conversão falhar, nunca levanta exceção.
    Útil para st.metric e qualquer lugar que precise de números legíveis.
    """
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v)


def fmt_pct(v, decimals: int = 1) -> str:
    """Formata percentual com casas decimais (ex.: 87.5 → '87.5%')."""
    try:
        return f"{float(v):.{decimals}f}%"
    except (TypeError, ValueError):
        return str(v)
