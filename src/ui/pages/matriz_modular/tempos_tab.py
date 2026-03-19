from __future__ import annotations

import pandas as pd
import streamlit as st

from .insights import _fmt_duration_from_hours


def render_tempos_tab(
    *,
    sb,
    tenant_id,
    revisao_id,
    eq_ids,
    tarefas,
    svc_ids_rank,
    svc_ids_all,
    all_services,
    eq_label_short,
    eq_label,
) -> None:
    st.markdown("### ⏱️ Tempos de execução (D/R/M)")
    st.caption("Análise de duração entre as etapas Desmontagem → Revisão → Montagem.")

    svc_ids_tempos = svc_ids_rank if svc_ids_rank else svc_ids_all
    tempos_rows = []
    try:
        tempos_rows = (
            sb.table("v_tarefas_etapas_duracoes")
            .select(
                "equipamento_id,servico_id,dt_inicio,dt_etapa_d,dt_etapa_r,dt_etapa_m,"
                "horas_d_para_r,horas_r_para_m,horas_d_para_m,horas_total"
            )
            .eq("tenant_id", tenant_id)
            .eq("revisao_id", revisao_id)
            .in_("equipamento_id", eq_ids)
            .execute()
            .data
        ) or []
    except Exception:
        tempos_rows = []

    df_t = pd.DataFrame(tempos_rows) if tempos_rows else pd.DataFrame(tarefas)
    if not tempos_rows:
        for col in ["dt_inicio", "dt_etapa_d", "dt_etapa_r", "dt_etapa_m"]:
            if col not in df_t.columns:
                df_t[col] = pd.NaT
            df_t[col] = pd.to_datetime(df_t[col], errors="coerce", utc=True)
        df_t["horas_d_para_r"] = (
            df_t["dt_etapa_r"] - df_t["dt_etapa_d"]
        ).dt.total_seconds() / 3600
        df_t["horas_r_para_m"] = (
            df_t["dt_etapa_m"] - df_t["dt_etapa_r"]
        ).dt.total_seconds() / 3600
        df_t["horas_d_para_m"] = (
            df_t["dt_etapa_m"] - df_t["dt_etapa_d"]
        ).dt.total_seconds() / 3600
        df_t["horas_total"] = (
            df_t["dt_etapa_m"] - df_t["dt_inicio"]
        ).dt.total_seconds() / 3600

    if "servico_id" in df_t.columns:
        df_t = df_t[df_t["servico_id"].isin(svc_ids_tempos)].copy()

    view_agg = pd.DataFrame()
    if df_t.empty:
        st.info("Sem dados de tempo ainda. Marque etapas D/R/M com timestamps para começar.")
        return view_agg

    sv_map = {s["id"]: (s.get("nome") or str(s["id"])) for s in all_services if s.get("id")}
    df_t["Frota"] = df_t["equipamento_id"].map(eq_label_short)
    df_t["Equipamento"] = df_t["equipamento_id"].map(eq_label)
    df_t["Serviço"] = df_t["servico_id"].map(sv_map).fillna(df_t["servico_id"].astype(str))
    for c in ["horas_d_para_r", "horas_r_para_m", "horas_d_para_m", "horas_total"]:
        if c in df_t.columns:
            df_t[c] = pd.to_numeric(df_t[c], errors="coerce")

    med_total = df_t["horas_total"].dropna().mean() if "horas_total" in df_t.columns else None
    med_dr = df_t["horas_d_para_r"].dropna().mean() if "horas_d_para_r" in df_t.columns else None
    med_rm = df_t["horas_r_para_m"].dropna().mean() if "horas_r_para_m" in df_t.columns else None
    completos_total = int(df_t["horas_total"].notna().sum()) if "horas_total" in df_t.columns else 0

    tk1, tk2, tk3, tk4 = st.columns(4)
    tk1.metric("Itens completos", str(completos_total))
    tk2.metric("Média total (D→M)", _fmt_duration_from_hours(med_total))
    tk3.metric("Média D→R", _fmt_duration_from_hours(med_dr))
    tk4.metric("Média R→M", _fmt_duration_from_hours(med_rm))
    st.divider()

    t_col1, t_col2 = st.columns([1, 1])
    with t_col1:
        st.markdown("#### Resumo por frota")
        agg = (
            df_t.groupby("Frota", dropna=False)
            .agg(
                itens=("servico_id", "count"),
                completos=("horas_total", lambda s: int(pd.Series(s).notna().sum())),
                media_total_h=("horas_total", "mean"),
                p90_total_h=(
                    "horas_total",
                    lambda s: float(pd.Series(s).dropna().quantile(0.9))
                    if pd.Series(s).dropna().shape[0]
                    else None,
                ),
                media_d_r_h=("horas_d_para_r", "mean"),
                media_r_m_h=("horas_r_para_m", "mean"),
            )
            .reset_index()
        )
        agg["Média Total"] = agg["media_total_h"].apply(_fmt_duration_from_hours)
        agg["P90"] = agg["p90_total_h"].apply(_fmt_duration_from_hours)
        agg["D→R"] = agg["media_d_r_h"].apply(_fmt_duration_from_hours)
        agg["R→M"] = agg["media_r_m_h"].apply(_fmt_duration_from_hours)
        view_agg_short = agg[["Frota", "itens", "completos", "Média Total", "P90", "D→R", "R→M"]].sort_values(
            ["completos", "itens"], ascending=[False, False]
        )
        agg2 = agg.copy()
        agg2["Equipamento"] = agg2["Frota"].map({v: eq_label.get(k, v) for k, v in eq_label_short.items()})
        view_agg = agg2[["Equipamento", "itens", "completos", "Média Total", "P90", "D→R", "R→M"]].sort_values(
            ["completos", "itens"], ascending=[False, False]
        )
        st.dataframe(
            view_agg_short.style.set_properties(
                subset=["Frota"], **{"text-align": "left", "font-weight": "600"}
            ).set_properties(**{"font-size": "12px"}),
            use_container_width=True,
            hide_index=True,
        )

    with t_col2:
        st.markdown("#### Gargalos — Top tempos")
        metric = st.selectbox("Ordenar por:", ["Total (D→M)", "D→R", "R→M"], index=0, key="tempo_metric")
        col_m = {"Total (D→M)": "horas_total", "D→R": "horas_d_para_r", "R→M": "horas_r_para_m"}[metric]
        top = df_t[["Frota", "Serviço", "horas_d_para_r", "horas_r_para_m", "horas_total"]].copy()
        top = top.dropna(subset=[col_m]).sort_values(by=[col_m], ascending=False).head(20)
        top["D→R"] = top["horas_d_para_r"].apply(_fmt_duration_from_hours)
        top["R→M"] = top["horas_r_para_m"].apply(_fmt_duration_from_hours)
        top["Total"] = top["horas_total"].apply(_fmt_duration_from_hours)
        st.dataframe(
            top[["Frota", "Serviço", "D→R", "R→M", "Total"]].style.set_properties(
                subset=["Frota", "Serviço"], **{"text-align": "left"}
            ).set_properties(**{"font-size": "12px"}),
            use_container_width=True,
            hide_index=True,
        )

    return view_agg
