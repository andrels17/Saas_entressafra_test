"""Home Overview — camada de transformação."""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from src.domain.kpi import calc_dept_kpis

# ── Schema fixo da Home ─────────────────────────────────────────────────
_HOME_SCHEMA_DEFAULTS: dict[str, Any] = {
    "grupo_id": None,
    "grupo_nome": None,
    "pct": 0.0,
    "eq_count": 0,
    "svc_count": 0,
    "done_steps": 0,
    "expected_steps": 0,
    "departamento_nome": None,
}


def enforce_home_schema(df_like) -> pd.DataFrame:
    """Garante colunas mínimas no DataFrame de KPIs da Home."""
    try:
        df = df_like if isinstance(
            df_like, pd.DataFrame) else pd.DataFrame(df_like)
    except Exception:
        df = pd.DataFrame()

    if df.empty:
        df = pd.DataFrame(columns=list(_HOME_SCHEMA_DEFAULTS.keys()))

    for col, default in _HOME_SCHEMA_DEFAULTS.items():
        if col not in df.columns:
            df[col] = default

    if df["grupo_id"].isna().all():
        df["grupo_id"] = df.index.astype(str)
    if df["grupo_nome"].isna().all():
        df["grupo_nome"] = df["grupo_id"].astype(str)

    for col in [
        "pct",
        "eq_count",
        "svc_count",
        "done_steps",
            "expected_steps"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df["pct"] = df["pct"].astype(float)
    return df


def norm_dt(x):
    try:
        if x is None or x == "":
            return None
        return pd.to_datetime(x, utc=True)
    except Exception:
        return None


def rev_start_end(
        rev: dict) -> tuple[pd.Timestamp | None, pd.Timestamp | None, int]:
    start = norm_dt(rev.get("data_inicio")) or norm_dt(rev.get("created_at"))
    end = norm_dt(rev.get("data_fim"))
    semanas = rev.get("semanas_total")
    try:
        semanas = int(semanas) if semanas is not None else None
    except Exception:
        semanas = None
    if semanas is None and start is not None and end is not None:
        days = max(1, int((end - start).days) + 1)
        semanas = int(math.ceil(days / 7.0))
    return start, end, max(1, int(semanas or 1))


def current_week(rev_start: pd.Timestamp | None, semanas_total: int) -> int:
    now = pd.Timestamp.utcnow()
    if rev_start is None:
        return 1
    try:
        rs = pd.Timestamp(rev_start)
        if rs.tzinfo is None:
            rs = rs.tz_localize("UTC")
        n = now.tz_localize(
            "UTC") if now.tzinfo is None else now.tz_convert("UTC")
        week = int(((n - rs).days // 7) + 1)
    except Exception:
        week = 1
    return max(1, min(int(semanas_total or 1), int(week or 1)))


def enrich_kdf(
    kdf: pd.DataFrame,
    gid_to_name: dict,
    gid_to_dept: dict,
    dep_scope_ids: list | None,
    grp_scope_ids: list | None,
) -> pd.DataFrame:
    """Enriquece o DataFrame de KPIs com nomes e filtra por escopo."""
    kdf = kdf.copy()
    kdf["Grupo"] = kdf["grupo_id"].map(
        gid_to_name).fillna(kdf["grupo_id"].astype(str))
    kdf["departamento_id"] = kdf["grupo_id"].map(gid_to_dept)
    if dep_scope_ids:
        kdf = kdf[kdf["departamento_id"].isin(dep_scope_ids)].copy()
    if grp_scope_ids:
        kdf = kdf[kdf["grupo_id"].isin(grp_scope_ids)].copy()
    return kdf


def compute_coverage(kdf: pd.DataFrame) -> dict:
    total_grupos = int(len(kdf))
    grupos_com_template = int((kdf["svc_count"] > 0).sum())
    grupos_com_peso = int(
        ((kdf["eq_count"] > 0) & (
            kdf["svc_count"] > 0)).sum())
    scope_w = kdf[(kdf["eq_count"] > 0) & (kdf["svc_count"] > 0)].copy()
    eq_total = int(
        pd.to_numeric(
            scope_w.get(
                "eq_count",
                0),
            errors="coerce").fillna(0).sum()) if not scope_w.empty else 0
    eq_done = int(pd.to_numeric(scope_w.loc[scope_w["pct"] >= 100, "eq_count"], errors="coerce").fillna(
        0).sum()) if not scope_w.empty else 0
    crit = int((scope_w["pct"] < 50).sum()) if not scope_w.empty else 0
    base = int(len(scope_w)) if not scope_w.empty else 0
    risco = int(round(crit / base * 100)) if base > 0 else 0
    return {
        "total_grupos": total_grupos,
        "grupos_com_template": grupos_com_template,
        "grupos_com_peso": grupos_com_peso,
        "eq_total": eq_total,
        "eq_done": eq_done,
        "risco_pct": risco,
    }


def compute_dept_summary(
        kdf: pd.DataFrame, gid_to_dept: dict) -> tuple[pd.DataFrame, int, int]:
    dsum = calc_dept_kpis(kdf, gid_to_dept)
    if dsum is None or getattr(dsum, "empty", True):
        return pd.DataFrame(), 0, 0
    dep_total = int(len(dsum))
    dep_done = int(
        (pd.to_numeric(
            dsum.get(
                "pct",
                0),
            errors="coerce").fillna(0) >= 100).sum())
    return dsum, dep_total, dep_done


def build_trend_chart_data(sdf: pd.DataFrame) -> pd.DataFrame:
    if sdf.empty:
        return pd.DataFrame()
    g = sdf.groupby(
        "week_number",
        as_index=False).agg(
        done=(
            "done_steps",
            "sum"),
        exp=(
            "expected_steps",
            "sum"))
    g["pct"] = (g["done"] / g["exp"] * 100).round(1).fillna(0).clip(0, 100)
    return g.sort_values("week_number")
