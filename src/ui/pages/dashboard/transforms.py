from __future__ import annotations

from typing import Any

import pandas as pd

from src.domain.kpi import calc_risco
from src.utils.dataframe import normalize_df
from src.utils.timezone import now_brt as _now_brt


def _ensure_df(value: Any) -> pd.DataFrame:
    """Aceita DataFrame, lista de dicts, dict ou None e sempre retorna DataFrame."""
    if isinstance(value, pd.DataFrame):
        return value.copy()
    if value is None:
        return pd.DataFrame()
    if isinstance(value, list):
        if not value:
            return pd.DataFrame()
        return pd.DataFrame(value)
    if isinstance(value, dict):
        return pd.DataFrame([value])
    try:
        return pd.DataFrame(value)
    except Exception:
        return pd.DataFrame()


def _series_or_default(df: pd.DataFrame, col: str, default=0):
    if col in df.columns:
        return df[col]
    return pd.Series([default] * len(df), index=df.index)


def normalize_matriz_base(raw: Any, eq_meta: Any) -> pd.DataFrame:
    """Normaliza base da matriz aceitando tanto DataFrame quanto lista de dicts."""
    raw_df = _ensure_df(raw)
    eq_meta_df = _ensure_df(eq_meta)

    if raw_df.empty:
        return pd.DataFrame(
            columns=[
                "tenant_id",
                "revisao_id",
                "departamento_id",
                "grupo_id",
                "grupo",
                "equipamento_id",
                "frota",
                "modelo",
                "servico_id",
                "setor",
                "state",
                "ok_count",
                "na",
                "trav",
                "updated_at",
                "data_inicio",
                "data_fim",
            ]
        )

    df = normalize_df(
        raw_df,
        rename_map={
            "grupo_nome": "grupo",
            "setor_nome": "setor",
            "estado_execucao": "state",
            "status": "state",
            "etapas_ok": "ok_count",
            "equipamento_nome": "frota",
        },
        numeric_cols=["ok_count", "etapa_d", "etapa_r", "etapa_m"],
    )

    for col in ("updated_at", "data_inicio", "data_fim"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    etapa_cols = [c for c in ("etapa_d", "etapa_r", "etapa_m") if c in df.columns]
    if etapa_cols:
        ok = pd.Series(0.0, index=df.index)
        for col in etapa_cols:
            ok = ok.add(
                pd.to_numeric(df[col], errors="coerce").fillna(0).clip(0, 1),
                fill_value=0,
            )
        df["ok_count"] = ok
    else:
        df["ok_count"] = pd.to_numeric(_series_or_default(df, "ok_count", 0), errors="coerce").fillna(0)
    df["ok_count"] = df["ok_count"].clip(lower=0, upper=3)

    state = _series_or_default(df, "state", "pendente").astype(str).str.strip().str.lower()
    df["state"] = state.replace(
        {
            "em andamento": "em_andamento",
            "em-andamento": "em_andamento",
            "andamento": "em_andamento",
            "concluído": "concluido",
            "concluido": "concluido",
            "não aplica": "nao_aplica",
            "nao aplica": "nao_aplica",
        }
    )
    df["na"] = df["state"].eq("nao_aplica")
    df["trav"] = df["state"].eq("travado")

    if not eq_meta_df.empty:
        meta = eq_meta_df.copy().rename(
            columns={
                "id": "equipamento_id",
                "frota": "frota_meta",
                "modelo": "modelo_meta",
                "departamento_id": "departamento_id_meta",
            }
        )
        if "equipamento_id" in meta.columns and "equipamento_id" in df.columns:
            df = df.merge(meta, on="equipamento_id", how="left")
    else:
        df["frota_meta"] = pd.Series(dtype=object)
        df["modelo_meta"] = pd.Series(dtype=object)
        df["departamento_id_meta"] = pd.Series(dtype=object)

    if "frota" not in df.columns:
        df["frota"] = pd.Series(dtype=object)
    if "modelo" not in df.columns:
        df["modelo"] = pd.Series(dtype=object)
    if "departamento_id" not in df.columns:
        df["departamento_id"] = pd.Series(dtype=object)

    df["frota"] = df["frota"].fillna(df.get("frota_meta")).fillna("—")
    df["modelo"] = df["modelo"].fillna(df.get("modelo_meta")).fillna("—")
    df["departamento_id"] = df["departamento_id"].fillna(df.get("departamento_id_meta"))
    df["grupo"] = _series_or_default(df, "grupo", "—").fillna("—")
    df["setor"] = _series_or_default(df, "setor", "—").fillna("—")
    return df


def apply_filters(
    df: pd.DataFrame,
    departamento_ids=None,
    grupo_ids=None,
    equipamento_ids=None,
) -> pd.DataFrame:
    f = df.copy()
    if departamento_ids is not None and "departamento_id" in f.columns:
        f = f[f["departamento_id"].isin(departamento_ids)]
    if grupo_ids is not None and "grupo_id" in f.columns:
        f = f[f["grupo_id"].isin(grupo_ids)]
    if equipamento_ids and "equipamento_id" in f.columns:
        f = f[f["equipamento_id"].isin(equipamento_ids)]
    return f


def _valid_scope(base: pd.DataFrame) -> pd.DataFrame:
    if base is None or base.empty:
        return pd.DataFrame(columns=list(base.columns) if isinstance(base, pd.DataFrame) else [])
    valid = base.copy()
    if "na" in valid.columns:
        valid = valid[~valid["na"].astype(bool)]
    return valid


def overall_from_base(base: pd.DataFrame) -> dict:
    if base is None or base.empty:
        return {"pct": 0.0, "total": 0, "concl": 0, "pend": 0, "andamento": 0, "trav": 0, "na": 0}
    valid = _valid_scope(base)
    total = int(len(valid))
    pct = round(float(valid["ok_count"].sum()) / max(total * 3, 1) * 100, 1) if total else 0.0
    return {
        "pct": float(max(0, min(100, pct))),
        "total": total,
        "concl": int((valid["state"] == "concluido").sum()),
        "pend": int((valid["state"] == "pendente").sum()),
        "andamento": int((valid["state"] == "em_andamento").sum()),
        "trav": int((valid["state"] == "travado").sum()),
        "na": int(base["na"].sum()) if "na" in base.columns else 0,
    }


def group_progress(base: pd.DataFrame) -> pd.DataFrame:
    if base is None or base.empty:
        return pd.DataFrame(columns=["grupo", "grupo_id", "departamento_id", "pct_concluido", "done_steps", "expected_steps"])
    valid = _valid_scope(base)
    rows = []
    for (gid, grupo, dept), sub in valid.groupby(["grupo_id", "grupo", "departamento_id"], dropna=False):
        expected = int(len(sub) * 3)
        done = float(sub["ok_count"].sum())
        pct = round(done / max(expected, 1) * 100, 1) if expected else 0.0
        rows.append({
            "grupo": grupo,
            "grupo_id": gid,
            "departamento_id": dept,
            "pct_concluido": max(0.0, min(100.0, pct)),
            "done_steps": int(round(done)),
            "expected_steps": expected,
        })
    return pd.DataFrame(rows)


def sector_progress(base: pd.DataFrame) -> pd.DataFrame:
    if base is None or base.empty:
        return pd.DataFrame(columns=["setor", "pct_concluido", "done_steps", "expected_steps"])
    valid = _valid_scope(base)
    rows = []
    for setor, sub in valid.groupby("setor", dropna=False):
        expected = int(len(sub) * 3)
        done = float(sub["ok_count"].sum())
        pct = round(done / max(expected, 1) * 100, 1) if expected else 0.0
        rows.append({
            "setor": setor,
            "pct_concluido": max(0.0, min(100.0, pct)),
            "done_steps": int(round(done)),
            "expected_steps": expected,
        })
    return pd.DataFrame(rows)


def equipment_progress(base: pd.DataFrame) -> pd.DataFrame:
    if base is None or base.empty:
        return pd.DataFrame(columns=["equipamento_id", "frota", "modelo", "pct_concluido", "done_steps", "expected_steps", "state"])
    valid = _valid_scope(base)
    rows = []
    for eid, sub in valid.groupby("equipamento_id", dropna=False):
        expected = int(len(sub) * 3)
        done = float(sub["ok_count"].sum())
        pct = round(done / max(expected, 1) * 100, 1) if expected else 0.0
        last_state = sub["state"].mode().iloc[0] if "state" in sub.columns and not sub["state"].mode().empty else "pendente"
        rows.append({
            "equipamento_id": eid,
            "frota": sub["frota"].iloc[0] if "frota" in sub.columns else "—",
            "modelo": sub["modelo"].iloc[0] if "modelo" in sub.columns else "—",
            "pct_concluido": max(0.0, min(100.0, pct)),
            "done_steps": int(round(done)),
            "expected_steps": expected,
            "state": last_state,
        })
    return pd.DataFrame(rows)


def risk_summary(base: pd.DataFrame) -> dict:
    if base is None or base.empty:
        return {"score": 0.0, "criticidade": "baixo", "itens_criticos": 0}
    risk = calc_risco(base)
    if isinstance(risk, dict):
        return risk
    return {"score": 0.0, "criticidade": "baixo", "itens_criticos": 0}


def recent_activity(base: pd.DataFrame, limit: int = 10) -> pd.DataFrame:
    if base is None or base.empty or "updated_at" not in base.columns:
        return pd.DataFrame(columns=["equipamento_id", "frota", "setor", "state", "updated_at"])
    out = base.copy().sort_values("updated_at", ascending=False)
    cols = [c for c in ["equipamento_id", "frota", "setor", "state", "updated_at"] if c in out.columns]
    return out[cols].head(limit).reset_index(drop=True)


def atraso_snapshot(base: pd.DataFrame) -> dict:
    if base is None or base.empty:
        return {"atrasados": 0, "andamento": 0, "concluidos": 0}
    state = _series_or_default(base, "state", "pendente").astype(str)
    return {
        "atrasados": int(state.eq("travado").sum()),
        "andamento": int(state.eq("em_andamento").sum()),
        "concluidos": int(state.eq("concluido").sum()),
    }


def freshness(base: pd.DataFrame) -> dict:
    if base is None or base.empty or "updated_at" not in base.columns:
        return {"last_update": None, "minutes": None, "stale": True}
    last_update = pd.to_datetime(base["updated_at"], errors="coerce").max()
    if pd.isna(last_update):
        return {"last_update": None, "minutes": None, "stale": True}
    now = _now_brt()
    if getattr(last_update, "tzinfo", None) is None:
        last_update = last_update.tz_localize(now.tzinfo)
    minutes = max(0, int((now - last_update).total_seconds() // 60))
    return {"last_update": last_update, "minutes": minutes, "stale": minutes > 60}
