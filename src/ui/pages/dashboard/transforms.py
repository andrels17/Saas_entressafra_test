"""Dashboard — camada de transformação (normalização + cálculos).

Responsabilidade única: receber DataFrames brutos e devolver estruturas
prontas para renderização. Sem I/O, sem Streamlit.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from src.domain.kpi import calc_risco
from src.utils.dataframe import normalize_df, pct_series, compact_top


# ── Normalização de views brutas ──────────────────────────────────────────────

def normalize_matriz_base(raw: pd.DataFrame, eq_meta: pd.DataFrame) -> pd.DataFrame:
    """Normaliza mv_matriz_base e enriquece com metadados de equipamentos."""
    if raw is None or raw.empty:
        return pd.DataFrame(columns=[
            "tenant_id", "revisao_id", "departamento_id", "grupo_id", "grupo",
            "equipamento_id", "frota", "modelo", "servico_id", "setor",
            "state", "ok_count", "na", "trav", "updated_at",
        ])

    df = normalize_df(
        raw,
        rename_map={
            "grupo_nome":       "grupo",
            "setor_nome":       "setor",
            "estado_execucao":  "state",
            "etapas_ok":        "ok_count",
        },
        numeric_cols=["ok_count"],
    )

    if "updated_at" in df.columns:
        df["updated_at"] = pd.to_datetime(df["updated_at"], errors="coerce")

    df["na"]   = df.get("state", pd.Series(dtype=object)).eq("nao_aplica")
    df["trav"] = df.get("state", pd.Series(dtype=object)).eq("travado")

    # Enriquece com metadados de equipamento
    if not eq_meta.empty:
        df = df.merge(eq_meta, on="equipamento_id", how="left")
    else:
        df["frota"]           = df.get("equipamento_nome", pd.Series(dtype=object)).fillna("—")
        df["modelo"]          = "—"
        df["departamento_id"] = None

    # Garante colunas essenciais
    for col, default in [("grupo", "—"), ("frota", "—"), ("modelo", "—"), ("setor", "—"), ("departamento_id", None)]:
        if col not in df.columns:
            df[col] = default
        else:
            if col in ("grupo", "frota", "modelo", "setor"):
                df[col] = df[col].fillna(df.get(f"{col}_nome_meta", pd.Series(dtype=object))).fillna(default)

    return df


def normalize_heatmap(df: pd.DataFrame) -> pd.DataFrame:
    return normalize_df(
        df,
        rename_map={"grupo_nome": "grupo", "setor_nome": "setor", "criticidade_score": "calor_score"},
        defaults={"grupo": "—", "setor": "—", "calor_score": 0},
        numeric_cols=["calor_score"],
        fillna={"grupo": "—", "setor": "—"},
    ) if df is not None and not df.empty else pd.DataFrame(columns=["grupo", "setor", "calor_score"])


def normalize_criticidade(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza dados de criticidade por equipamento."""
    COLS = ["ranking_criticidade", "Equipamento", "grupo", "criticidade_score", "travados", "pendentes", "pct_concluido"]
    if df is None or df.empty:
        return pd.DataFrame(columns=COLS)

    out = normalize_df(
        df,
        rename_map={
            "ranking_pos":          "ranking_criticidade",
            "equipamento_nome":     "Equipamento",
            "grupo_nome":           "grupo",
            "percentual_concluido": "pct_concluido",
        },
        defaults={
            "Equipamento":          "—",
            "grupo":                "—",
            "ranking_criticidade":  None,
            "criticidade_score":    0,
            "travados":             0,
            "pendentes":            0,
            "pct_concluido":        0,
        },
        numeric_cols=["criticidade_score", "pct_concluido", "travados", "pendentes"],
        fillna={"Equipamento": "—", "grupo": "—"},
    )

    # Gera ranking se ausente
    out["ranking_criticidade"] = pd.to_numeric(out["ranking_criticidade"], errors="coerce")
    if out["ranking_criticidade"].isna().any():
        ranked = (
            out.sort_values(
                ["criticidade_score", "pct_concluido", "Equipamento"],
                ascending=[False, True, True],
            )
            .reset_index(drop=True)
        )
        ranked["ranking_criticidade"] = range(1, len(ranked) + 1)
        out = ranked

    out["ranking_criticidade"] = pd.to_numeric(out["ranking_criticidade"], errors="coerce").fillna(999999).astype(int)
    out["travados"]  = out["travados"].astype(int)
    out["pendentes"] = out["pendentes"].astype(int)
    return out


def normalize_timeline(df: pd.DataFrame) -> pd.DataFrame:
    COLS = ["dia", "concluidos", "restantes", "movimentacoes"]
    if df is None or df.empty:
        return pd.DataFrame(columns=COLS)

    out = normalize_df(
        df,
        rename_map={"data": "dia", "dt": "dia", "date": "dia",
                    "concluido": "concluidos", "restante": "restantes", "movs": "movimentacoes"},
        defaults={"dia": pd.NaT, "concluidos": 0, "restantes": 0, "movimentacoes": 0},
        numeric_cols=["concluidos", "restantes", "movimentacoes"],
    )
    out = out.loc[:, ~pd.Index(out.columns).duplicated()].copy()
    out["dia"] = pd.to_datetime(out["dia"], errors="coerce")
    return out[out["dia"].notna()].copy()


# ── Cálculos de progresso ─────────────────────────────────────────────────────

def apply_filters(df: pd.DataFrame, departamento_ids=None, grupo_ids=None, equipamento_ids=None) -> pd.DataFrame:
    f = df.copy()
    if departamento_ids and "departamento_id" in f.columns:
        f = f[f["departamento_id"].isin(departamento_ids)]
    if grupo_ids and "grupo_id" in f.columns:
        f = f[f["grupo_id"].isin(grupo_ids)]
    if equipamento_ids and "equipamento_id" in f.columns:
        f = f[f["equipamento_id"].isin(equipamento_ids)]
    return f


def overall_from_base(base: pd.DataFrame) -> dict:
    if base.empty:
        return {"pct": 0.0, "total": 0, "concl": 0, "pend": 0, "andamento": 0, "trav": 0, "na": 0}
    total = int(len(base))
    pct   = round(float(base["ok_count"].sum()) / max(total * 3, 1) * 100, 1)
    return {
        "pct":       pct,
        "total":     total,
        "concl":     int((base["state"] == "concluido").sum()),
        "pend":      int((base["state"] == "pendente").sum()),
        "andamento": int((base["state"] == "em_andamento").sum()),
        "trav":      int((base["state"] == "travado").sum()),
        "na":        int(base["na"].sum()) if "na" in base.columns else 0,
    }


def group_progress(base: pd.DataFrame) -> pd.DataFrame:
    if base.empty:
        return pd.DataFrame(columns=["grupo", "grupo_id", "departamento_id", "pct_concluido"])
    rows = []
    for (gid, grupo, dept), sub in base.groupby(["grupo_id", "grupo", "departamento_id"], dropna=False):
        pct = round(float(sub["ok_count"].sum()) / max(len(sub) * 3, 1) * 100, 1)
        rows.append({"grupo": grupo, "grupo_id": gid, "departamento_id": dept, "pct_concluido": pct})
    return pd.DataFrame(rows)


def sector_progress(base: pd.DataFrame) -> pd.DataFrame:
    if base.empty:
        return pd.DataFrame(columns=["setor", "pct_concluido"])
    rows = []
    for setor, sub in base.groupby("setor", dropna=False):
        pct = round(float(sub["ok_count"].sum()) / max(len(sub) * 3, 1) * 100, 1)
        rows.append({"setor": setor, "pct_concluido": pct})
    return pd.DataFrame(rows)


def equipment_progress(base: pd.DataFrame) -> pd.DataFrame:
    COLS = ["equipamento_id", "grupo_id", "grupo", "departamento_id", "Frota", "Modelo",
            "Total", "% Concluído", "Pendentes", "Em andamento", "Travados", "Não aplica", "Concluídos"]
    if base.empty:
        return pd.DataFrame(columns=COLS)
    rows = []
    for (eid, gid, grupo, dept, frota, modelo), sub in base.groupby(
        ["equipamento_id", "grupo_id", "grupo", "departamento_id", "frota", "modelo"], dropna=False
    ):
        rows.append({
            "equipamento_id": eid,
            "grupo_id":       gid,
            "grupo":          grupo,
            "departamento_id":dept,
            "Frota":          frota,
            "Modelo":         modelo,
            "Total":          int(len(sub)),
            "% Concluído":    round(float(sub["ok_count"].sum()) / max(len(sub) * 3, 1) * 100, 1),
            "Pendentes":      int((sub["state"] == "pendente").sum()),
            "Em andamento":   int((sub["state"] == "em_andamento").sum()),
            "Travados":       int((sub["state"] == "travado").sum()),
            "Não aplica":     int((sub["state"] == "nao_aplica").sum()),
            "Concluídos":     int((sub["state"] == "concluido").sum()),
        })
    return pd.DataFrame(rows)


def build_inteligencia(base: pd.DataFrame) -> tuple[dict, dict, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Computa risco, previsão, heatmap, criticidade e timeline a partir da base.

    Retorna:
        (risco_dict, previsao_dict, heatmap_df, criticidade_df, timeline_df)
    """
    EMPTY_RISCO = {
        "risco_score": 0.0, "pct_concluido": 0.0, "pendentes": 0,
        "travados": 0, "em_andamento": 0, "concluidos": 0, "status_risco": "baixo",
    }
    EMPTY_PREV = {
        "data_inicio": None, "data_fim_planejada": None, "dias_passados": 0,
        "dias_planejados": 0, "percentual_concluido": 0.0, "ritmo_medio_dia": 0.0,
        "dias_estimados_total": 0.0, "dias_restantes_estimados": 0.0,
        "previsao_termino": None, "status_previsao": "sem_base",
    }
    EMPTY_HEAT = pd.DataFrame(columns=["grupo", "setor", "calor_score"])
    EMPTY_CRIT = pd.DataFrame(columns=["ranking_criticidade", "Equipamento", "grupo", "criticidade_score", "travados", "pendentes", "pct_concluido"])
    EMPTY_TL   = pd.DataFrame(columns=["dia", "movimentacoes", "concluidos", "restantes"])

    if base is None or base.empty:
        return EMPTY_RISCO, EMPTY_PREV, EMPTY_HEAT, EMPTY_CRIT, EMPTY_TL

    b = base.copy()
    if "updated_at" in b.columns:
        b["updated_at"] = pd.to_datetime(b["updated_at"], errors="coerce")
    if "na" not in b.columns:
        b["na"] = False
    valid = b[~b["na"].astype(bool)].copy()

    total_valid   = len(valid)
    etapas_ok     = float(valid["ok_count"].sum()) if "ok_count" in valid.columns else 0.0
    pct           = round(etapas_ok / max(total_valid * 3, 1) * 100, 1)
    pend          = int((valid["state"] == "pendente").sum())    if "state" in valid.columns else 0
    trav          = int((valid["state"] == "travado").sum())     if "state" in valid.columns else 0
    andamento     = int((valid["state"] == "em_andamento").sum())if "state" in valid.columns else 0
    concl         = int((valid["state"] == "concluido").sum())   if "state" in valid.columns else 0

    risco = calc_risco(
        travados=trav, pendentes=pend, em_andamento=andamento,
        concluidos=concl, total=total_valid, pct_concluido=pct,
    )

    # Previsão de término
    data_inicio = pd.to_datetime(b["data_inicio"], errors="coerce").dropna().min() if "data_inicio" in b.columns else pd.NaT
    data_fim    = pd.to_datetime(b["data_fim"],    errors="coerce").dropna().min() if "data_fim"    in b.columns else pd.NaT
    dias_passados  = int(max((pd.Timestamp.today().normalize() - data_inicio.normalize()).days, 0)) if pd.notna(data_inicio) else 0
    dias_planejados = int(max((data_fim.normalize() - data_inicio.normalize()).days, 0)) if pd.notna(data_inicio) and pd.notna(data_fim) else 0
    ritmo          = round(pct / max(dias_passados, 1), 4) if pct > 0 else 0.0
    dias_est_total = round(100.0 / ritmo, 2) if ritmo > 0 else 0.0
    dias_rest      = round(max(dias_est_total - dias_passados, 0), 2) if ritmo > 0 else 0.0
    prev_termino   = (data_inicio + pd.to_timedelta(int(round(dias_est_total)), unit="D")) if (pd.notna(data_inicio) and ritmo > 0) else pd.NaT
    status_prev    = "sem_base" if pct <= 0 else ("no_prazo" if (pd.notna(prev_termino) and (pd.isna(data_fim) or prev_termino <= data_fim)) else "atraso")
    previsao: dict[str, Any] = {
        "data_inicio":               None if pd.isna(data_inicio) else data_inicio,
        "data_fim_planejada":        None if pd.isna(data_fim)    else data_fim,
        "dias_passados":             dias_passados,
        "dias_planejados":           dias_planejados,
        "percentual_concluido":      pct,
        "ritmo_medio_dia":           ritmo,
        "dias_estimados_total":      dias_est_total,
        "dias_restantes_estimados":  dias_rest,
        "previsao_termino":          None if pd.isna(prev_termino) else prev_termino,
        "status_previsao":           status_prev,
    }

    # Heatmap
    heat = EMPTY_HEAT
    if not valid.empty and "grupo" in valid.columns and "setor" in valid.columns:
        heat = (
            valid.groupby(["grupo", "setor"], dropna=False)
            .apply(lambda s: (
                (s["state"] == "travado").sum() * 3.0
                + (s["state"] == "pendente").sum() * 1.5
                + (s["state"] == "em_andamento").sum() * 1.0
            ) / max(len(s), 1))
            .reset_index(name="calor_score")
        )

    # Criticidade por equipamento
    crit_rows: list[dict] = []
    if not valid.empty:
        for eid, sub in valid.groupby("equipamento_id", dropna=False):
            score  = round(((sub["state"] == "travado").sum() * 3.0 + (sub["state"] == "pendente").sum() * 1.5 + (sub["state"] == "em_andamento").sum() * 1.0) / max(len(sub), 1), 2)
            pct_eq = round(float(sub["ok_count"].sum()) / max(len(sub) * 3, 1) * 100, 1)
            crit_rows.append({
                "equipamento_id":    eid,
                "Equipamento":       str(sub["frota"].iloc[0] if "frota" in sub.columns else "—"),
                "grupo":             str(sub["grupo"].iloc[0] if "grupo" in sub.columns else "—"),
                "criticidade_score": score,
                "travados":          int((sub["state"] == "travado").sum()),
                "pendentes":         int((sub["state"] == "pendente").sum()),
                "pct_concluido":     pct_eq,
            })
    crit = pd.DataFrame(crit_rows)
    if not crit.empty:
        crit = crit.sort_values(["criticidade_score", "pct_concluido", "Equipamento"], ascending=[False, True, True], kind="stable").reset_index(drop=True)
        crit["ranking_criticidade"] = range(1, len(crit) + 1)

    # Timeline
    tl = EMPTY_TL
    if "updated_at" in valid.columns:
        vt = valid[valid["updated_at"].notna()].copy()
        if not vt.empty:
            vt["dia"] = vt["updated_at"].dt.floor("D")
            tl_rows = [
                {"dia": dia, "movimentacoes": int(len(sub)),
                 "concluidos": int((sub["state"] == "concluido").sum()),
                 "restantes":  int((sub["state"] != "concluido").sum())}
                for dia, sub in vt.groupby("dia", dropna=False)
            ]
            tl = pd.DataFrame(tl_rows).sort_values("dia")

    return dict(risco), previsao, heat, crit, tl


def fmt_date(v) -> str:
    if pd.isna(v) or v is None:
        return "—"
    try:
        return pd.to_datetime(v).strftime("%d/%m/%Y")
    except Exception:
        return str(v)
