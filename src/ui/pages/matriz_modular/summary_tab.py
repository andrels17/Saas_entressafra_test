from __future__ import annotations

import streamlit as st

from src.ui.pages.matriz_runtime import risk_color as _risk_color


def render_summary_tab(*, resumo_df):
    st.markdown("### Ranking de equipamentos por progresso")
    st.caption("Ordenado do mais atrasado para o mais adiantado.")
    if resumo_df.empty:
        st.info("Sem dados de resumo para esta revisão.")
        return

    rk1, rk2, rk3, rk4 = st.columns(4)
    rk1.metric("Total equip.", len(resumo_df))
    rk2.metric("100% concluídos", int((resumo_df["%"] >= 100).sum()))
    rk3.metric("Progresso médio", f"{int(resumo_df['%'].mean())}%")
    rk4.metric("Sem início (0%)", int((resumo_df["%"] == 0).sum()))
    st.markdown("---")

    for _, row in resumo_df.iterrows():
        pct_r = int(row["%"])
        color = _risk_color(pct_r)
        c1r, c2r = st.columns([0.6, 0.4])
        with c1r:
            st.markdown(
                f'<div style="font-size:.88rem;font-weight:600;margin-bottom:3px">{row["Equipamento"]}</div>'
                f'<div style="background:rgba(255,255,255,.08);border-radius:4px;height:7px">'
                f'<div style="width:{pct_r}%;background:{color};height:7px;border-radius:4px;transition:width .4s"></div></div>',
                unsafe_allow_html=True,
            )
        with c2r:
            done_lbl = int(row["Concluidos"])
            tot_lbl = int(row["Total"])
            st_lbl = "✅ Concluído" if pct_r >= 100 else ("🔴 Sem início" if pct_r == 0 else f"🟡 {pct_r}%")
            st.markdown(
                f'<div style="font-size:.82rem;color:rgba(255,255,255,.65);padding-top:3px">'
                f'<span style="color:{color};font-weight:700">{pct_r}%</span>'
                f'  ·  {done_lbl}/{tot_lbl} etapas'
                f'  <span style="opacity:.6">{st_lbl}</span></div>',
                unsafe_allow_html=True,
            )
