from __future__ import annotations

import pandas as pd
import streamlit as st

from .pdf_export import _build_pdf_tables, _df_to_csv_bytes, _reportlab_available

def _extract_semana_revisao(*dfs):
    """Retorna ultima semana encontrada + 1, a partir dos dataframes disponíveis."""
    import re

    for df in dfs:
        if not isinstance(df, pd.DataFrame) or df.empty:
            continue

        for col in df.columns:
            if "semana" in str(col).lower():
                try:
                    vals = (
                        df[col]
                        .astype(str)
                        .str.extract(r"(\d+)", expand=False)
                    )
                    nums = pd.to_numeric(vals, errors="coerce").dropna()
                    if not nums.empty:
                        return int(nums.max()) + 1
                except Exception:
                    continue
    return None



def render_export_tab(
    *,
    tenant_id,
    grupo_id,
    revisao_id,
    titulo,
    grupo_nome,
    resumo_df,
    view_agg,
    sector_tables_for_export,
    data_version,
) -> None:
    st.markdown("### Exportações")
    res_exp = resumo_df if (isinstance(resumo_df, pd.DataFrame) and not resumo_df.empty) else pd.DataFrame()
    va_exp = view_agg if (isinstance(view_agg, pd.DataFrame) and not view_agg.empty) else pd.DataFrame()

    n_res = len(res_exp) if not res_exp.empty else 0
    n_va = len(va_exp) if not va_exp.empty else 0
    n_set = len(sector_tables_for_export)

    c1e, c2e = st.columns(2)
    with c1e:
        st.caption(f"📋 Resumo por equipamento — {n_res} linha(s)")
        res_sorted = (
            res_exp.sort_values(
                by=[c for c in ["Score", "%", "Equipamento"] if c in res_exp.columns],
                ascending=[False, True, True][: sum(1 for c in ["Score", "%", "Equipamento"] if c in res_exp.columns)],
            )
            if not res_exp.empty
            else res_exp
        )
        st.download_button(
            "⬇️ Baixar resumo (CSV)",
            data=_df_to_csv_bytes(res_sorted) if not res_exp.empty else b"",
            file_name=f"resumo_{grupo_nome}.csv".replace("/", "-"),
            mime="text/csv",
            use_container_width=True,
            disabled=res_exp.empty,
        )
    with c2e:
        va_label = "por tarefa" if ("Serviço" in va_exp.columns and not va_exp.empty) else ""
        st.caption(f"⏱️ Tempos de execução {va_label} — {n_va} linha(s)")
        st.download_button(
            "⬇️ Baixar tempos (CSV)",
            data=_df_to_csv_bytes(va_exp) if not va_exp.empty else b"",
            file_name=f"tempos_{grupo_nome}.csv".replace("/", "-"),
            mime="text/csv",
            use_container_width=True,
            disabled=va_exp.empty,
        )

    st.divider()
    st.markdown("#### PDF completo")
    if n_set == 0:
        st.warning("Nenhum dado de setor disponível para gerar o PDF. Verifique se há equipamentos e template configurados.")
        return
    if not _reportlab_available():
        st.info("Instale `reportlab` no requirements.txt para habilitar a exportação em PDF.")
        return

    st.caption(f"📄 Relatório atualizado • Grupo: {grupo_nome} • Revisão: {titulo}")
    st.caption(f"Relatório com {n_set} setor(es) · {n_res} equipamento(s)")

    export_signature = (
        str(tenant_id),
        str(grupo_id),
        str(revisao_id),
        str(data_version),
        int(n_res),
        int(n_set),
    )
    prev_signature = st.session_state.get("mtz_pdf_export_signature")
    if prev_signature != export_signature:
        st.session_state.pop("mtz_pdf_export_bytes", None)
        st.session_state["mtz_pdf_export_signature"] = export_signature
        st.session_state["mtz_pdf_export_ready"] = False

    if not st.session_state.get("mtz_pdf_export_ready", False):
        with st.spinner("Gerando PDF otimizado..."):
            resumo_pdf_df = resumo_df.copy() if isinstance(resumo_df, pd.DataFrame) else pd.DataFrame()
            sector_tables_pdf = [(setor_nome, setor_df.copy()) for setor_nome, setor_df in (sector_tables_for_export or [])]
            semana_revisao = _extract_semana_revisao(
                resumo_pdf_df,
                va_exp if isinstance(va_exp, pd.DataFrame) else pd.DataFrame(),
                pd.concat(
                    [df for _, df in sector_tables_pdf if isinstance(df, pd.DataFrame)],
                    ignore_index=True,
                    sort=False,
                ) if sector_tables_pdf else pd.DataFrame(),
            )

            st.session_state["mtz_pdf_export_bytes"] = _build_pdf_tables(
                titulo=titulo,
                grupo_nome=grupo_nome,
                resumo_df=resumo_pdf_df,
                sector_tables=sector_tables_pdf,
                semana_revisao=semana_revisao,
            )
            st.session_state["mtz_pdf_export_ready"] = True
        st.success("PDF pronto 🚀")

    pdf_bytes = st.session_state["mtz_pdf_export_bytes"]
    pdf_file_name = f"relatorio_matriz_{grupo_nome}.pdf".replace("/", "-")
    st.download_button(
        "⬇️ Baixar PDF completo",
        data=pdf_bytes,
        file_name=pdf_file_name,
        mime="application/pdf",
        use_container_width=True,
        type="primary",
        key=f"mtz_pdf_download_{grupo_id}_{revisao_id}_{n_res}_{n_set}",
        help="Baixa o relatório completo já otimizado e sincronizado com os dados atuais.",
    )
