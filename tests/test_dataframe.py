"""Testes unitários para src/utils/dataframe.py."""
import pandas as pd
import pytest

from src.utils.dataframe import normalize_df, safe_numeric, pct_series, compact_top


# ── normalize_df ──────────────────────────────────────────────────────────────

def test_normalize_rename():
    df = pd.DataFrame({"grupo_nome": ["A", "B"]})
    out = normalize_df(df, rename_map={"grupo_nome": "grupo"})
    assert "grupo" in out.columns
    assert "grupo_nome" not in out.columns

def test_normalize_rename_nao_sobrescreve():
    df = pd.DataFrame({"grupo_nome": ["A"], "grupo": ["Z"]})
    out = normalize_df(df, rename_map={"grupo_nome": "grupo"})
    assert list(out["grupo"]) == ["Z"]  # não sobrescreve coluna existente

def test_normalize_defaults_adiciona_coluna():
    df = pd.DataFrame({"id": [1, 2]})
    out = normalize_df(df, defaults={"score": 0})
    assert "score" in out.columns
    assert list(out["score"]) == [0, 0]

def test_normalize_defaults_nao_toca_existente():
    df = pd.DataFrame({"score": [5]})
    out = normalize_df(df, defaults={"score": 0})
    assert list(out["score"]) == [5]

def test_normalize_numeric_cols():
    df = pd.DataFrame({"val": ["1", "abc", None]})
    out = normalize_df(df, numeric_cols=["val"])
    assert out["val"].tolist() == [1.0, 0.0, 0.0]

def test_normalize_fillna():
    df = pd.DataFrame({"nome": [None, "B"]})
    out = normalize_df(df, fillna={"nome": "—"})
    assert out["nome"].tolist() == ["—", "B"]

def test_normalize_none_retorna_vazio():
    out = normalize_df(None)
    assert isinstance(out, pd.DataFrame)
    assert out.empty

def test_normalize_dataframe_vazio():
    out = normalize_df(pd.DataFrame(), defaults={"x": 0})
    assert out.empty  # colunas não adicionadas a df vazio (comportamento esperado)


# ── safe_numeric ──────────────────────────────────────────────────────────────

def test_safe_numeric_converte():
    s   = pd.Series(["1", "2.5", "abc", None])
    out = safe_numeric(s)
    assert out.tolist() == [1.0, 2.5, 0.0, 0.0]

def test_safe_numeric_fill_customizado():
    s   = pd.Series(["x"])
    out = safe_numeric(s, fill=-1.0)
    assert out.tolist() == [-1.0]


# ── pct_series ────────────────────────────────────────────────────────────────

def test_pct_series_normal():
    done  = pd.Series([6.0, 3.0])
    total = pd.Series([6.0, 6.0])
    out   = pct_series(done, total)
    assert out.tolist() == [100.0, 50.0]

def test_pct_series_zero_total():
    done  = pd.Series([0.0])
    total = pd.Series([0.0])
    out   = pct_series(done, total)
    assert out.tolist() == [0.0]  # clip(lower=1) evita divisão por zero

def test_pct_series_clamp_100():
    done  = pd.Series([999.0])
    total = pd.Series([1.0])
    out   = pct_series(done, total)
    assert out.tolist() == [100.0]


# ── compact_top ───────────────────────────────────────────────────────────────

def test_compact_top_seleciona_colunas():
    df  = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6], "c": [7, 8, 9]})
    out = compact_top(df, ["a", "b"], n=2)
    assert list(out.columns) == ["a", "b"]
    assert len(out) == 2

def test_compact_top_ignora_colunas_ausentes():
    df  = pd.DataFrame({"a": [1]})
    out = compact_top(df, ["a", "z"])
    assert list(out.columns) == ["a"]

def test_compact_top_dataframe_vazio():
    out = compact_top(pd.DataFrame(), ["a", "b"])
    assert out.empty


# ── fmt_int ───────────────────────────────────────────────────────────────────

from src.utils.dataframe import fmt_int, fmt_pct


def test_fmt_int_normal():
    assert fmt_int(12345) == "12,345"

def test_fmt_int_zero():
    assert fmt_int(0) == "0"

def test_fmt_int_float_truncado():
    assert fmt_int(9999.9) == "9,999"

def test_fmt_int_string_numerica():
    assert fmt_int("1000") == "1,000"

def test_fmt_int_invalido_nao_levanta():
    result = fmt_int("abc")
    assert isinstance(result, str)

def test_fmt_int_none_nao_levanta():
    result = fmt_int(None)
    assert isinstance(result, str)


# ── fmt_pct ───────────────────────────────────────────────────────────────────

def test_fmt_pct_normal():
    assert fmt_pct(87.5) == "87.5%"

def test_fmt_pct_zero():
    assert fmt_pct(0) == "0.0%"

def test_fmt_pct_cem():
    assert fmt_pct(100.0) == "100.0%"

def test_fmt_pct_casas_decimais():
    assert fmt_pct(33.333, decimals=2) == "33.33%"

def test_fmt_pct_invalido_nao_levanta():
    result = fmt_pct("x")
    assert isinstance(result, str)
