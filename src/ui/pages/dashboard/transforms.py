from __future__ import annotations

from typing import Any

import pandas as pd

from src.domain.kpi import calc_risco
from src.utils.dataframe import normalize_df
from src.utils.timezone import now_brt as _now_brt


def _ensure_df(value: Any) -> pd.DataFrame:
    if isinstance(value, pd.DataFrame):
        return value.copy()
    if value is None:
        return pd.DataFrame()
    if isinstance(value, list):
        return pd.DataFrame(value) if value else pd.DataFrame()
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


def normalize_matriz_base(raw: Any,
                          eq_meta: Any) -> pd.DataFrame:
    """Normaliza mv_matriz_base e enriquece com metadados de equipamentos."""
    raw = _ensure_df(raw)
    eq_meta = _ensure_df(eq_meta)

    if raw.empty:
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
            ])

    df = normalize_df(
        raw,
        rename_map={
            "grupo_nome": "grupo",
            "setor_nome": "setor",
            "estado_execucao": "state",
            "etapas_ok": "ok_count",
            "equipamento_nome": "frota",
        },
        numeric_cols=["ok_count", "etapa_d", "etapa_r", "etapa_m"],
    )

    for col in ("updated_at", "data_inicio", "data_fim"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    etapa_cols = [
        c for c in (
            "etapa_d",
            "etapa_r",
            "etapa_m") if c in df.columns]
    if etapa_cols:
        ok = pd.Series(0.0, index=df.index)
        for col in etapa_cols:
            ok = ok.add(
                pd.to_numeric(
                    df[col],
                    errors="coerce").fillna(0).clip(
                    0,
                    1),
                fill_value=0)
        df["ok_count"] = ok
    else:
        df["ok_count"] = pd.to_numeric(_series_or_default(
            df, "ok_count", 0), errors="coerce").fillna(0)
    df["ok_count"] = df["ok_count"].clip(lower=0, upper=3)

    state = _series_or_default(df, "state", "pendente").astype(
        str).str.strip().str.lower()
    df["state"] = state.replace({
        "em andamento": "em_andamento",
        "em-andamento": "em_andamento",
        "andamento": "em_andamento",
        "concluído": "concluido",
        "não aplica": "nao_aplica",
        "nao aplica": "nao_aplica",
    })
    df["na"] = df["state"].eq("nao_aplica")
    df["trav"] = df["state"].eq("travado")

    if not eq_meta.empty:
        meta = eq_meta.copy().rename(columns={
            "id": "equipamento_id",
            "frota": "frota_meta",
            "modelo": "modelo_meta",
            "departamento_id": "departamento_id_meta",
        })
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
    df["departamento_id"] = df["departamento_id"].fillna(
        df.get("departamento_id_meta"))
    df["grupo"] = _series_or_default(df, "grupo", "—").fillna("—")
    df["setor"] = _series_or_default(df, "setor", "—").fillna("—")

    # Normaliza colunas de ID para str para garantir compatibilidade com
    # todos os dicionários de lookup (dept_map, gid_to_dept, etc.) que usam
    # str(uuid) como chave. UUID objects do Supabase falham silenciosamente
    # no map() quando as chaves são strings.
    for _id_col in ("departamento_id", "grupo_id", "equipamento_id"):
        if _id_col in df.columns:
            df[_id_col] = df[_id_col].map(
                lambda v: str(v) if pd.notna(v) and v is not None else None
            )
    return df


def apply_filters(
        df: pd.DataFrame,
        departamento_ids=None,
        grupo_ids=None,
        equipamento_ids=None) -> pd.DataFrame:
    f = df.copy()

    if departamento_ids not in (None, []) and "departamento_id" in f.columns:
        dep_ids = {str(x) for x in departamento_ids if x is not None}
        f = f[f["departamento_id"].map(lambda v: str(v) if pd.notna(v) else None).isin(dep_ids)]

    if grupo_ids not in (None, []) and "grupo_id" in f.columns:
        grp_ids = {str(x) for x in grupo_ids if x is not None}
        f = f[f["grupo_id"].map(lambda v: str(v) if pd.notna(v) else None).isin(grp_ids)]

    if equipamento_ids not in (None, []) and "equipamento_id" in f.columns:
        eq_ids = {str(x) for x in equipamento_ids if x is not None}
        f = f[f["equipamento_id"].map(lambda v: str(v) if pd.notna(v) else None).isin(eq_ids)]

    return f


def _valid_scope(base: pd.DataFrame) -> pd.DataFrame:
    if base is None or base.empty:
        return pd.DataFrame(
            columns=list(
                base.columns) if isinstance(
                base, pd.DataFrame) else [])
    valid = base.copy()
    if "na" in valid.columns:
        valid = valid[~valid["na"].astype(bool)]
    return valid


def overall_from_base(base: pd.DataFrame) -> dict:
    if base is None or base.empty:
        return {
            "pct": 0.0,
            "total": 0,
            "concl": 0,
            "sem_inicio": 0,
            "andamento": 0,
            "atrasados": 0,
            # chaves legadas para compatibilidade
            "pend": 0,
            "trav": 0,
            "na": 0,
        }

    valid = _valid_scope(base)
    total_linhas = int(len(valid))
    pct = round(float(valid["ok_count"].sum()) / max(total_linhas * 3, 1) * 100) if total_linhas else 0.0

    eq_df = equipment_progress(base)
    if eq_df.empty:
        concl = sem_inicio = andamento = atrasados = 0
    else:
        progresso = pd.to_numeric(eq_df["% Concluído"], errors="coerce").fillna(0)
        concl = int((progresso >= 100).sum())
        sem_inicio = int((progresso <= 0).sum())
        andamento = int(((progresso > 0) & (progresso < 100)).sum())
        atrasados = 0
        if "data_fim" in valid.columns:
            hoje = pd.Timestamp(_now_brt()).normalize().tz_localize(None)
            atrasados_ids = set()
            for eid, sub in valid.groupby("equipamento_id", dropna=False):
                data_fim_eq = pd.to_datetime(sub.get("data_fim"), errors="coerce").dropna()
                if data_fim_eq.empty:
                    continue
                prazo = data_fim_eq.max().normalize()
                expected = int(len(sub) * 3)
                pct_eq = round(float(sub["ok_count"].sum()) / max(expected, 1) * 100, 1) if expected else 0.0
                if hoje > prazo and pct_eq < 100:
                    atrasados_ids.add(eid)
            atrasados = len(atrasados_ids)

    return {
        "pct": float(max(0, min(100, pct))),
        "total": int(len(eq_df)) if not eq_df.empty else 0,
        "concl": concl,
        "sem_inicio": sem_inicio,
        "andamento": andamento,
        "atrasados": atrasados,
        # chaves legadas para evitar quebrar trechos antigos do render
        "pend": sem_inicio,
        "trav": atrasados,
        "na": int(base["na"].sum()) if "na" in base.columns else 0,
    }


def group_progress(base: pd.DataFrame) -> pd.DataFrame:
    if base is None or base.empty:
        return pd.DataFrame(
            columns=[
                "grupo",
                "grupo_id",
                "departamento_id",
                "pct_concluido",
                "done_steps",
                "expected_steps"])
    valid = _valid_scope(base)
    rows = []
    for (gid, grupo, dept), sub in valid.groupby(
            ["grupo_id", "grupo", "departamento_id"], dropna=False):
        expected = int(len(sub) * 3)
        done = float(sub["ok_count"].sum())
        pct = round(done / max(expected, 1) * 100) if expected else 0
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
        return pd.DataFrame(
            columns=[
                "setor",
                "pct_concluido",
                "done_steps",
                "expected_steps"])
    valid = _valid_scope(base)
    rows = []
    for setor, sub in valid.groupby("setor", dropna=False):
        expected = int(len(sub) * 3)
        done = float(sub["ok_count"].sum())
        pct = round(done / max(expected, 1) * 100) if expected else 0
        rows.append({
            "setor": setor,
            "pct_concluido": max(0.0, min(100.0, pct)),
            "done_steps": int(round(done)),
            "expected_steps": expected,
        })
    return pd.DataFrame(rows)


def equipment_progress(base: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "equipamento_id",
        "grupo_id",
        "grupo",
        "departamento_id",
        "Frota",
        "Modelo",
        "Total",
        "% Concluído",
        "Pendentes",
        "Em andamento",
        "Travados",
        "Não aplica",
        "Concluídos",
        "done_steps",
        "expected_steps"]
    if base is None or base.empty:
        return pd.DataFrame(columns=cols)
    base = base.copy()
    base["frota"] = base.get(
        "frota",
        pd.Series(
            index=base.index,
            dtype=object)).fillna("—").astype(str).str.strip()
    base["modelo"] = base.get(
        "modelo",
        pd.Series(
            index=base.index,
            dtype=object)).fillna("—").astype(str).str.strip()
    rows = []
    for (eid, gid, grupo, dept, frota, modelo), sub in base.groupby(
        ["equipamento_id", "grupo_id", "grupo", "departamento_id", "frota", "modelo"], dropna=False
    ):
        valid = sub[~sub["na"].astype(bool)] if "na" in sub.columns else sub
        expected = int(len(valid) * 3)
        done = float(valid["ok_count"].sum()) if not valid.empty else 0.0
        pct = round(done / max(expected, 1) * 100) if expected else 0
        rows.append({
            "equipamento_id": eid,
            "grupo_id": gid,
            "grupo": grupo,
            "departamento_id": dept,
            "Frota": frota,
            "Modelo": modelo,
            "Total": int(len(sub)),
            "% Concluído": max(0.0, min(100.0, pct)),
            "Pendentes": int((valid["state"] == "pendente").sum()) if not valid.empty else 0,
            "Em andamento": int((valid["state"] == "em_andamento").sum()) if not valid.empty else 0,
            "Travados": int((valid["state"] == "travado").sum()) if not valid.empty else 0,
            "Não aplica": int((sub["state"] == "nao_aplica").sum()),
            "Concluídos": int((valid["state"] == "concluido").sum()) if not valid.empty else 0,
            "done_steps": int(round(done)),
            "expected_steps": expected,
        })
    return pd.DataFrame(rows)


def build_inteligencia(
        base: pd.DataFrame) -> tuple[dict, dict, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    EMPTY_RISCO = {
        "risco_score": 0.0,
        "pct_concluido": 0.0,
        "pendentes": 0,
        "travados": 0,
        "em_andamento": 0,
        "concluidos": 0,
        "status_risco": "baixo",
    }
    EMPTY_PREV = {
        "data_inicio": None,
        "data_fim_planejada": None,
        "dias_passados": 0,
        "dias_planejados": 0,
        "percentual_concluido": 0.0,
        "ritmo_medio_dia": 0.0,
        "dias_estimados_total": 0.0,
        "dias_restantes_estimados": 0.0,
        "previsao_termino": None,
        "status_previsao": "sem_base",
    }
    EMPTY_HEAT = pd.DataFrame(columns=["grupo", "setor", "calor_score"])
    EMPTY_CRIT = pd.DataFrame(
        columns=[
            "ranking_criticidade",
            "Equipamento",
            "grupo",
            "criticidade_score",
            "travados",
            "pendentes",
            "pct_concluido"])
    EMPTY_TL = pd.DataFrame(
        columns=[
            "dia",
            "movimentacoes",
            "concluidos",
            "restantes"])

    if base is None or base.empty:
        return EMPTY_RISCO, EMPTY_PREV, EMPTY_HEAT, EMPTY_CRIT, EMPTY_TL

    valid = _valid_scope(base)
    if valid.empty:
        return EMPTY_RISCO, EMPTY_PREV, EMPTY_HEAT, EMPTY_CRIT, EMPTY_TL

    total_valid = len(valid)
    etapas_ok = float(valid["ok_count"].sum())
    pct = max(0.0, min(100.0, round(etapas_ok / max(total_valid * 3, 1) * 100)))
    pend = int((valid["state"] == "pendente").sum())
    trav = int((valid["state"] == "travado").sum())
    andamento = int((valid["state"] == "em_andamento").sum())
    concl = int((valid["state"] == "concluido").sum())

    risco = calc_risco(
        travados=trav,
        pendentes=pend,
        em_andamento=andamento,
        concluidos=concl,
        total=total_valid,
        pct_concluido=pct)

    data_inicio = pd.to_datetime(valid["data_inicio"], errors="coerce").dropna().min() if "data_inicio" in valid.columns else pd.NaT
    if pd.isna(data_inicio) and "dt_inicio" in valid.columns:
        data_inicio = pd.to_datetime(valid["dt_inicio"], errors="coerce").dropna().min()

    data_fim = pd.to_datetime(valid["data_fim"], errors="coerce").dropna().max() if "data_fim" in valid.columns else pd.NaT
    hoje = pd.Timestamp(_now_brt()).normalize().tz_localize(None)

    dias_passados = int(max((hoje - data_inicio.normalize()).days, 0)) if pd.notna(data_inicio) else 0
    dias_planejados = int(max((data_fim.normalize() - data_inicio.normalize()).days, 0)) if pd.notna(data_inicio) and pd.notna(data_fim) else 0
    dias_rest_cal = int(max((data_fim.normalize() - hoje).days, 0)) if pd.notna(data_fim) else 0
    ritmo = round(max(100.0 - pct, 0.0) / max(dias_rest_cal, 1), 4) if (pct < 100 and dias_rest_cal > 0) else 0.0

    if pd.isna(data_fim):
        status_prev = "sem_prazo"
    elif pct >= 100:
        status_prev = "concluido"
    elif hoje > data_fim.normalize():
        status_prev = "atraso"
    elif hoje == data_fim.normalize():
        status_prev = "vence_hoje"
    else:
        status_prev = "no_prazo"

    previsao: dict[str, Any] = {
        "data_inicio": None if pd.isna(data_inicio) else data_inicio,
        "data_fim_planejada": None if pd.isna(data_fim) else data_fim,
        "dias_passados": dias_passados,
        "dias_planejados": dias_planejados,
        "percentual_concluido": pct,
        "ritmo_medio_dia": ritmo,
        "dias_estimados_total": float(dias_planejados),
        "dias_restantes_estimados": float(dias_rest_cal),
        "previsao_termino": None,
        "status_previsao": status_prev,
    }

    heat = (valid.groupby(["grupo", "setor"], dropna=False) .apply(lambda s: ((s["state"] == "travado").sum() *
                                                                              3.0 +
                                                                              (s["state"] == "pendente").sum() *
                                                                              1.5 +
                                                                              (s["state"] == "em_andamento").sum()) /
                                                                   max(len(s), 1)) .reset_index(name="calor_score")) if "grupo" in valid.columns and "setor" in valid.columns else EMPTY_HEAT

    crit_rows = []
    for eid, sub in valid.groupby("equipamento_id", dropna=False):
        expected = int(len(sub) * 3)
        pct_eq = round(float(sub["ok_count"].sum()) /
                       max(expected, 1) * 100, 1) if expected else 0.0
        crit_rows.append(
            {
                "equipamento_id": eid, "Equipamento": str(
                    sub["frota"].iloc[0] if "frota" in sub.columns else "—"), "grupo": str(
                    sub["grupo"].iloc[0] if "grupo" in sub.columns else "—"), "criticidade_score": round(
                    ((sub["state"] == "travado").sum() * 3.0 + (
                        sub["state"] == "pendente").sum() * 1.5 + (
                            sub["state"] == "em_andamento").sum()) / max(
                                len(sub), 1), 2), "travados": int(
                                    (sub["state"] == "travado").sum()), "pendentes": int(
                                        (sub["state"] == "pendente").sum()), "pct_concluido": max(
                                            0.0, min(
                                                100.0, pct_eq)), })
    crit = pd.DataFrame(crit_rows)
    if not crit.empty:
        crit = crit.sort_values(["criticidade_score", "pct_concluido", "Equipamento"], ascending=[
                                False, True, True]).reset_index(drop=True)
        crit["ranking_criticidade"] = range(1, len(crit) + 1)

    tl = EMPTY_TL
    if "updated_at" in valid.columns:
        vt = valid[valid["updated_at"].notna()].copy()
        if not vt.empty:
            vt["dia"] = vt["updated_at"].dt.floor("D")
            tl = vt.groupby("dia", dropna=False).agg(
                movimentacoes=("equipamento_id", "size"),
                concluidos=("state", lambda s: int((s == "concluido").sum())),
                restantes=("state", lambda s: int((s != "concluido").sum())),
            ).reset_index().sort_values("dia")

    return dict(risco), previsao, heat, crit, tl


def fmt_date(v) -> str:
    if pd.isna(v) or v is None:
        return "—"
    try:
        return pd.to_datetime(v).strftime("%d/%m/%Y")
    except Exception:
        return str(v)
