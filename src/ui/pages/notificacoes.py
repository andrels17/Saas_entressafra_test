"""Notificações — alertas proativos gerados automaticamente.

Funcionalidades:
  - Equipamentos travados há X dias sem atualização
  - Equipamentos sem nenhum apontamento na semana atual
  - Setores com 0% de progresso (nunca iniciados)
  - Equipamentos em risco de não concluir no prazo
  - Resumo de alertas por grupo/departamento
  - Exportação CSV de cada categoria
  - Exportação PDF consolidada de todos os alertas
"""
from __future__ import annotations

import io
from datetime import datetime

import pandas as pd
import streamlit as st

from src.auth.roles import Role
from src.auth.scope import get_my_scope
from src.ui.core.styles import page_header as _ph
from src.ui.components.feedback import notice_card, selection_summary
from src.ui.core.cache import bump_data_version, clear_cached_functions
from src.utils.supabase_helpers import (
    current_role, current_tenant_id, sb_for_user,
)
from src.utils.nav import get_current_revisao


# ── helpers ─────────────────────────────────────────────────────────────

def _risk_color(pct: int) -> str:
    if pct >= 80:
        return "#12B76A"
    if pct >= 50:
        return "#F59E0B"
    return "#EF4444"


@st.cache_data(ttl=60, show_spinner=False)
def _load_data(_tid: str, _rev_id: str, _ver: str = "0") -> dict:
    """Carrega todos os dados necessários para os alertas em uma só query."""
    sb = sb_for_user()
    try:
        tarefas = (
            sb.table("tarefas_servico")
            .select(
                "id,equipamento_id,servico_id,status,semana,"
                "etapa_d,etapa_r,etapa_m,observacao,"
                "dt_etapa_d,dt_etapa_r,dt_etapa_m,updated_at,"
                "servicos(id,nome,setor_id,setores(nome)),"
                "equipamentos(id,frota,modelo,grupo_id,"
                "equip_grupos(id,nome,departamento_id))"
            )
            .eq("tenant_id", _tid)
            .eq("revisao_id", _rev_id)
            .execute()
            .data
        ) or []
    except Exception:
        tarefas = []

    try:
        revisao = (
            sb.table("revisoes")
            .select("id,titulo,data_inicio,semanas_total,status")
            .eq("id", _rev_id)
            .limit(1)
            .execute()
            .data
        )
        revisao = revisao[0] if revisao else {}
    except Exception:
        revisao = {}

    return {"tarefas": tarefas, "revisao": revisao}


def _semana_atual(data_inicio_str: str | None, semanas_total: int) -> int:
    if not data_inicio_str:
        return 1
    try:
        inicio = pd.to_datetime(data_inicio_str, utc=True)
        agora = pd.Timestamp.utcnow()
        semana = max(1, int((agora - inicio).days // 7) + 1)
        return min(semana, semanas_total or semana)
    except Exception:
        return 1


def _dias_desde(ts_str: str | None) -> int | None:
    if not ts_str:
        return None
    try:
        ts = pd.to_datetime(ts_str, utc=True)
        return int((pd.Timestamp.utcnow() - ts).total_seconds() // 86400)
    except Exception:
        return None


# ── Processamento de alertas ────────────────────────────────────────────

def _build_alertas(tarefas: list[dict], revisao: dict,
                   dias_travado: int, dias_sem_update: int) -> dict:
    """Classifica cada tarefa em categorias de alerta."""
    semana_atual = _semana_atual(
        revisao.get("data_inicio"),
        revisao.get("semanas_total") or 99)
    semanas_total = revisao.get("semanas_total") or 99

    travados, sem_inicio, sem_update, risco_prazo = [], [], [], []

    # Agrupado por equipamento para calcular % por equip.
    eq_tasks: dict[str, list] = {}
    for t in tarefas:
        eq = (t.get("equipamentos") or {})
        eid = eq.get("id") or t.get("equipamento_id", "")
        eq_tasks.setdefault(eid, []).append(t)

    for eid, tasks in eq_tasks.items():
        eq = (tasks[0].get("equipamentos") or {})
        frota = eq.get("frota") or eid
        modelo = eq.get("modelo") or ""
        grupo = (eq.get("equip_grupos") or {}).get("nome") or "—"
        dept_id = (eq.get("equip_grupos") or {}).get("departamento_id")

        total = len(tasks) * 3
        done = sum(
            int(bool(t.get("etapa_d"))) +
            int(bool(t.get("etapa_r"))) +
            int(bool(t.get("etapa_m")))
            for t in tasks
        )
        pct = round((done / max(total, 1)) * 100)

        # Progresso esperado linear
        esperado_pct = round((semana_atual / max(semanas_total, 1)) * 100)

        base = {
            "Frota": frota,
            "Modelo": modelo,
            "Grupo": grupo,
            "dept_id": dept_id,
            "% Atual": pct,
            "% Esperado": esperado_pct,
        }

        for t in tasks:
            svc = (t.get("servicos") or {})
            setor = (svc.get("setores") or {}).get("nome") or "—"
            svc_nome = svc.get("nome") or "—"
            status = t.get("status") or "pendente"
            updated = t.get("updated_at") or t.get("dt_etapa_m") or t.get(
                "dt_etapa_r") or t.get("dt_etapa_d")
            dias = _dias_desde(updated)

            row = {**base, "Setor": setor, "Serviço": svc_nome,
                   "Status": status, "Obs.": t.get("observacao") or ""}

            # Travado há X dias
            if status == "travado" and (dias is None or dias >= dias_travado):
                travados.append(
                    {**row, "Dias travado": dias if dias is not None else "?"})

            # Sem nenhum apontamento (0 etapas)
            if not t.get("etapa_d") and not t.get(
                    "etapa_r") and not t.get("etapa_m"):
                if dias is None or dias >= dias_sem_update:
                    sem_inicio.append(
                        {**row, "Dias sem update": dias if dias is not None else "?"})

            # Sem atualização há muito tempo (não travado, não concluído)
            if status not in ("concluido", "nao_aplica", "travado"):
                if dias is not None and dias >= dias_sem_update:
                    sem_update.append({**row, "Dias parado": dias})

        # Risco de prazo: atrasado vs esperado
        if pct < esperado_pct - 15 and pct < 100:
            atraso = esperado_pct - pct
            risco_prazo.append({**base,
                                "Atraso (p.p.)": atraso,
                                "Etapas feitas": done,
                                "Etapas total": total,
                                })

    return {
        "travados": pd.DataFrame(travados) if travados else pd.DataFrame(),
        "sem_inicio": pd.DataFrame(sem_inicio) if sem_inicio else pd.DataFrame(),
        "sem_update": pd.DataFrame(sem_update) if sem_update else pd.DataFrame(),
        "risco_prazo": pd.DataFrame(risco_prazo)if risco_prazo else pd.DataFrame(),
        "semana_atual": semana_atual,
        "semanas_total": semanas_total,
    }


def _resumo_por_grupo(alertas: dict) -> pd.DataFrame:
    """Consolida contagem de alertas por grupo."""
    grupos: dict[str, dict] = {}
    for categoria, col in [
        ("travados", "travados"),
        ("sem_inicio", "sem_inicio"),
        ("sem_update", "parados"),
        ("risco_prazo", "risco_prazo"),
    ]:
        df = alertas.get(categoria, pd.DataFrame())
        if df.empty or "Grupo" not in df.columns:
            continue
        for grupo, cnt in df["Grupo"].value_counts().items():
            grupos.setdefault(str(grupo),
                              {"Grupo": str(grupo),
                               "Travados": 0,
                               "Sem início": 0,
                               "Parados": 0,
                               "Risco prazo": 0})
            key_map = {"travados": "Travados", "sem_inicio": "Sem início",
                       "sem_update": "Parados", "risco_prazo": "Risco prazo"}
            grupos[str(grupo)][key_map[categoria]] = int(cnt)
    if not grupos:
        return pd.DataFrame()
    df_res = pd.DataFrame(list(grupos.values()))
    df_res["Total alertas"] = df_res[["Travados",
                                      "Sem início", "Parados", "Risco prazo"]].sum(axis=1)
    return df_res.sort_values(
        "Total alertas",
        ascending=False).reset_index(
        drop=True)


def _build_pdf_alertas(alertas: dict, revisao: dict) -> bytes:
    """Gera PDF consolidado de todos os alertas."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                        Table, TableStyle)
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_RIGHT
    except Exception:
        return b""

    sty = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=sty["Heading1"], fontSize=14, leading=18,
                        textColor=colors.HexColor("#111827"), spaceAfter=2)
    h2 = ParagraphStyle(
        "h2",
        parent=sty["Heading2"],
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#111827"),
        spaceBefore=8,
        spaceAfter=3)
    p = ParagraphStyle("p", parent=sty["BodyText"], fontSize=9, leading=12,
                       textColor=colors.HexColor("#374151"))
    sm = ParagraphStyle("sm", parent=sty["BodyText"], fontSize=8, leading=10,
                        textColor=colors.grey)

    buf = io.BytesIO()
    PAGE = A4
    MARGIN = 1.5 * cm
    pw = PAGE[0] - 2 * MARGIN
    doc = SimpleDocTemplate(buf, pagesize=PAGE,
                            leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=MARGIN, bottomMargin=MARGIN)

    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    ts_style = ParagraphStyle(
        "ts",
        parent=sty["BodyText"],
        fontSize=8,
        alignment=TA_RIGHT,
        textColor=colors.HexColor("#6B7280"))

    header_data = [[
        Paragraph("Relatório de Alertas — Notificações", h1),
        Paragraph(f"Emitido em<br/>{now_str}", ts_style),
    ]]
    header_t = Table(
        header_data,
        colWidths=[
            pw - 3.5 * cm,
            3.5 * cm],
        rowHeights=[
            1 * cm])
    header_t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 1.0, colors.HexColor("#E5E7EB")),
    ]))

    meta_lbl = ParagraphStyle("ml", parent=sty["BodyText"], fontSize=8,
                              textColor=colors.HexColor("#6B7280"))
    meta_val = ParagraphStyle(
        "mv",
        parent=sty["BodyText"],
        fontSize=10,
        textColor=colors.HexColor("#111827"),
        fontName="Helvetica-Bold")
    meta_data = [
        [Paragraph("Revisão", meta_lbl), Paragraph("Semana", meta_lbl)],
        [Paragraph(revisao.get("titulo") or "—", meta_val),
         Paragraph(f'{alertas["semana_atual"]} / {alertas["semanas_total"]}', meta_val)],
    ]
    meta_t = Table(meta_data, colWidths=[pw * 0.6, pw * 0.4])
    meta_t.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 1), (-1, 1), 0.5, colors.HexColor("#E5E7EB")),
    ]))

    # KPIs
    n_trav = len(alertas["travados"])
    n_sem = len(alertas["sem_inicio"])
    n_upd = len(alertas["sem_update"])
    n_risc = len(alertas["risco_prazo"])
    kpi_data = [[
        Paragraph(f'<font color="#6B7280" size="8">🚫 Travados</font><br/>'
                  f'<b><font size="16" color="#EF4444">{n_trav}</font></b>', p),
        Paragraph(f'<font color="#6B7280" size="8">⬜ Sem início</font><br/>'
                  f'<b><font size="16">{n_sem}</font></b>', p),
        Paragraph(f'<font color="#6B7280" size="8">⏸ Parados</font><br/>'
                  f'<b><font size="16">{n_upd}</font></b>', p),
        Paragraph(f'<font color="#6B7280" size="8">⚠️ Risco prazo</font><br/>'
                  f'<b><font size="16" color="#F59E0B">{n_risc}</font></b>', p),
    ]]
    kpi_t = Table(kpi_data, colWidths=[pw / 4] * 4, rowHeights=[1.4 * cm])
    kpi_t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
        ("LINEAFTER", (0, 0), (2, 0), 0.5, colors.HexColor("#E5E7EB")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F9FAFB")),
    ]))

    story = [header_t, Spacer(1, 0.3 * cm), meta_t, Spacer(1, 0.4 * cm),
             kpi_t, Spacer(1, 0.5 * cm)]

    def _df_table(df: pd.DataFrame, cols_show: list, title: str,
                  accent: tuple = (colors.HexColor("#111827"), colors.white)):
        story.append(Paragraph(title, h2))
        if df.empty:
            story.append(Paragraph("Nenhum item nesta categoria.", sm))
            story.append(Spacer(1, 0.2 * cm))
            return
        cols_ok = [c for c in cols_show if c in df.columns]
        if not cols_ok:
            story.append(Paragraph("Sem dados.", sm))
            return
        story.append(Paragraph(f"{len(df)} item(s) encontrado(s).", sm))
        story.append(Spacer(1, 0.1 * cm))
        data_rows = [cols_ok] + df[cols_ok].fillna("").values.tolist()
        # Equipamento/Frota gets more space if present
        cw = []
        for i, c in enumerate(cols_ok):
            if c in ("Frota", "Equipamento"):
                cw.append(pw * 0.18)
            elif c in ("Modelo", "Serviço", "Setor", "Obs."):
                cw.append(pw * 0.20)
            else:
                cw.append(pw * 0.12)
        # Normalize widths
        total_w = sum(cw)
        cw = [w * pw / total_w for w in cw]

        t = Table(data_rows, colWidths=cw, repeatRows=1)
        ts = [
            ("BACKGROUND", (0, 0), (-1, 0), accent[0]),
            ("TEXTCOLOR", (0, 0), (-1, 0), accent[1]),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("FONTSIZE", (0, 1), (-1, -1), 7.5),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F9FAFB")]),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#374151")),
        ]
        t.setStyle(TableStyle(ts))
        story.append(t)
        story.append(Spacer(1, 0.4 * cm))

    _df_table(alertas["travados"],
              ["Frota",
               "Modelo",
               "Grupo",
               "Setor",
               "Serviço",
               "Dias travado",
               "Obs."],
              "🚫 Travados sem resolução",
              (colors.HexColor("#7F1D1D"),
               colors.white))
    _df_table(alertas["sem_inicio"],
              ["Frota",
               "Modelo",
               "Grupo",
               "Setor",
               "Serviço",
               "Dias sem update"],
              "⬜ Sem nenhum apontamento",
              (colors.HexColor("#1E3A5F"),
               colors.white))
    _df_table(alertas["sem_update"],
              ["Frota",
               "Modelo",
               "Grupo",
               "Setor",
               "Serviço",
               "Status",
               "Dias parado"],
              "⏸ Parados (sem atualização)",
              (colors.HexColor("#374151"),
               colors.white))
    _df_table(alertas["risco_prazo"],
              ["Frota",
               "Modelo",
               "Grupo",
               "% Atual",
               "% Esperado",
               "Atraso (p.p.)"],
              "⚠️ Risco de não concluir no prazo",
              (colors.HexColor("#78350F"),
               colors.white))

    def _footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawRightString(
            PAGE[0] - MARGIN,
            0.8 * cm,
            f"Página {
                canvas.getPageNumber()}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


# ── Fragmentos de UI ────────────────────────────────────────────────────

def _df_download(df: pd.DataFrame, label: str, fname: str) -> None:
    if df.empty:
        return
    cols_pub = [c for c in df.columns if c != "dept_id"]
    st.download_button(
        f"⬇️ {label}",
        data=df[cols_pub].to_csv(index=False).encode("utf-8"),
        file_name=fname,
        mime="text/csv",
        use_container_width=True,
        key=f"dl_{fname}",
    )


@st.fragment
def _fragment_resumo(alertas: dict, revisao: dict) -> None:
    semana = alertas["semana_atual"]
    total_s = alertas["semanas_total"]
    n_trav = len(alertas["travados"])
    n_sem = len(alertas["sem_inicio"])
    n_upd = len(alertas["sem_update"])
    n_risc = len(alertas["risco_prazo"])

    st.markdown(
        f'<div style="font-size:.85rem;color:rgba(255,255,255,.55);margin-bottom:8px">'
        f'Semana <b style="color:#fff">{semana}</b> de <b style="color:#fff">{total_s}</b>'
        f' · Revisão: <b style="color:#FFD100">{revisao.get("titulo", "—")}</b></div>',
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🚫 Travados", n_trav, delta="crítico" if n_trav else "ok",
              delta_color="inverse" if n_trav else "off")
    c2.metric("⬜ Sem início", n_sem, delta="atenção" if n_sem else "ok",
              delta_color="inverse" if n_sem else "off")
    c3.metric("⏸ Parados", n_upd, delta="atenção" if n_upd else "ok",
              delta_color="inverse" if n_upd else "off")
    c4.metric(
        "⚠️ Risco de prazo",
        n_risc,
        delta="atraso vs meta" if n_risc else "no prazo",
        delta_color="inverse" if n_risc else "off")

    if n_trav == 0 and n_sem == 0 and n_upd == 0 and n_risc == 0:
        st.success("✅ Nenhum alerta ativo com os thresholds configurados.")


@st.fragment
def _fragment_travados(df: pd.DataFrame) -> None:
    st.markdown("### 🚫 Travados sem resolução")
    if df.empty:
        st.success("Nenhum item travado no período configurado.")
        return
    st.caption(f"{len(df)} tarefa(s) travada(s) sem resolução.")
    cols_show = [
        c for c in [
            "Frota",
            "Modelo",
            "Grupo",
            "Setor",
            "Serviço",
            "Dias travado",
            "Obs."] if c in df.columns]
    st.dataframe(
        df[cols_show].sort_values(
            "Dias travado",
            ascending=False) if "Dias travado" in df.columns else df[cols_show],
        use_container_width=True,
        hide_index=True,
        column_config={
            "Dias travado": st.column_config.NumberColumn(
                "Dias travado",
                help="Dias desde última atualização"),
        })
    _df_download(df, "Exportar CSV", "alertas_travados.csv")


@st.fragment
def _fragment_sem_inicio(df: pd.DataFrame) -> None:
    st.markdown("### ⬜ Sem nenhum apontamento")
    if df.empty:
        st.success("Todos os itens tiveram pelo menos um apontamento.")
        return
    st.caption(f"{len(df)} tarefa(s) sem nenhuma etapa marcada.")
    cols_show = [
        c for c in [
            "Frota",
            "Modelo",
            "Grupo",
            "Setor",
            "Serviço",
            "Dias sem update"] if c in df.columns]
    st.dataframe(df[cols_show], use_container_width=True, hide_index=True)
    _df_download(df, "Exportar CSV", "alertas_sem_inicio.csv")


@st.fragment
def _fragment_parados(df: pd.DataFrame) -> None:
    st.markdown("### ⏸ Parados (sem atualização)")
    if df.empty:
        st.success("Nenhum item parado no período configurado.")
        return
    st.caption(f"{len(df)} tarefa(s) sem atualização no período.")
    cols_show = [
        c for c in [
            "Frota",
            "Modelo",
            "Grupo",
            "Setor",
            "Serviço",
            "Status",
            "Dias parado"] if c in df.columns]
    st.dataframe(
        df[cols_show].sort_values(
            "Dias parado",
            ascending=False) if "Dias parado" in df.columns else df[cols_show],
        use_container_width=True,
        hide_index=True,
        column_config={
            "Dias parado": st.column_config.NumberColumn("Dias parado"),
            "Status": st.column_config.TextColumn("Status"),
        })
    _df_download(df, "Exportar CSV", "alertas_parados.csv")


@st.fragment
def _fragment_risco_prazo(df: pd.DataFrame) -> None:
    st.markdown("### ⚠️ Risco de não concluir no prazo")
    if df.empty:
        st.success("Todos os equipamentos estão dentro da meta linear.")
        return
    st.caption(
        f"{len(df)} equipamento(s) com atraso acima de 15 p.p. em relação à meta.")
    cols_show = [
        c for c in [
            "Frota",
            "Modelo",
            "Grupo",
            "% Atual",
            "% Esperado",
            "Atraso (p.p.)",
            "Etapas feitas",
            "Etapas total"] if c in df.columns]
    df_show = df[cols_show].sort_values(
        "Atraso (p.p.)",
        ascending=False) if "Atraso (p.p.)" in df.columns else df[cols_show]
    st.dataframe(
        df_show,
        use_container_width=True,
        hide_index=True,
        column_config={
            "% Atual": st.column_config.ProgressColumn(
                "% Atual",
                min_value=0,
                max_value=100),
            "% Esperado": st.column_config.ProgressColumn(
                "% Esperado",
                min_value=0,
                max_value=100),
            "Atraso (p.p.)": st.column_config.NumberColumn(
                "Atraso (p.p.)",
                help="Diferença entre esperado e atual"),
        })
    _df_download(df, "Exportar CSV", "alertas_risco_prazo.csv")


@st.fragment
def _fragment_resumo_grupos(alertas: dict) -> None:
    """Resumo consolidado de alertas por grupo."""
    st.markdown("### 📊 Resumo por grupo")
    df_res = _resumo_por_grupo(alertas)
    if df_res.empty:
        st.info("Nenhum alerta encontrado para exibir por grupo.")
        return
    st.caption(f"{len(df_res)} grupo(s) com alertas ativos.")

    # Mini-cards por grupo
    for _, row in df_res.iterrows():
        total = int(row.get("Total alertas", 0))
        trav = int(row.get("Travados", 0))
        sem = int(row.get("Sem início", 0))
        par = int(row.get("Parados", 0))
        risc = int(row.get("Risco prazo", 0))
        color = "#EF4444" if trav > 0 else (
            "#F59E0B" if (par + risc) > 0 else "#F59E0B")
        badges = []
        if trav:
            badges.append(
                f'<span style="background:rgba(239,68,68,.2);color:#EF4444;padding:2px 8px;border-radius:999px;font-size:.78rem">🚫 {trav} travado{
                    "s" if trav > 1 else ""}</span>')
        if sem:
            badges.append(
                f'<span style="background:rgba(107,114,128,.2);color:#9CA3AF;padding:2px 8px;border-radius:999px;font-size:.78rem">⬜ {sem} sem início</span>')
        if par:
            badges.append(
                f'<span style="background:rgba(245,158,11,.2);color:#F59E0B;padding:2px 8px;border-radius:999px;font-size:.78rem">⏸ {par} parado{
                    "s" if par > 1 else ""}</span>')
        if risc:
            badges.append(
                f'<span style="background:rgba(245,158,11,.2);color:#F59E0B;padding:2px 8px;border-radius:999px;font-size:.78rem">⚠️ {risc} risco prazo</span>')
        badges_html = " ".join(badges)
        st.markdown(
            f'<div style="padding:10px 14px;border-radius:10px;border:1px solid rgba(255,255,255,.08);'
            f'background:rgba(255,255,255,.03);margin-bottom:8px">'
            f'<div style="font-size:.9rem;font-weight:700;margin-bottom:6px">'
            f'{row["Grupo"]} <span style="color:{color};font-size:.8rem">({total} alerta{"s" if total > 1 else ""})</span></div>'
            f'<div style="display:flex;flex-wrap:wrap;gap:6px">{badges_html}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Exportar CSV do resumo
    cols_pub = [c for c in df_res.columns if c != "dept_id"]
    st.download_button(
        "⬇️ Exportar resumo por grupo (CSV)",
        data=df_res[cols_pub].to_csv(
            index=False).encode("utf-8"),
        file_name="alertas_resumo_grupos.csv",
        mime="text/csv",
        use_container_width=True,
        key="dl_resumo_grupos")


@st.fragment
def _fragment_disparo_manual(tenant_id: str, revisao_id: str, is_admin: bool,
                             dias_travado: int, dias_sem_update: int) -> None:
    """UI de disparo manual de e-mail semanal por departamento."""

    if not is_admin:
        st.info("Apenas administradores podem disparar o envio de e-mails.")
        return

    # ── 1. Status do SMTP ───────────────────────────────────────────────────
    smtp_ok = True
    smtp_erro = ""
    try:
        from src.services.email.smtp_sender import _load_config_from_secrets
        _load_config_from_secrets()
    except Exception as e:
        smtp_ok = False
        smtp_erro = str(e)

    col_smtp, col_dest = st.columns(2)
    with col_smtp:
        if smtp_ok:
            st.success("✅ SMTP configurado")
        else:
            st.error("❌ SMTP não configurado")
            with st.expander("Ver como configurar", expanded=False):
                st.markdown("**Microsoft (Outlook / Office 365):**")
                st.code(
                    'SMTP_HOST = "smtp.office365.com"\n'
                    'SMTP_PORT = "587"\n'
                    'SMTP_USER = "seu@empresa.com.br"\n'
                    'SMTP_PASSWORD = "sua_senha"\n'
                    'SMTP_FROM_NAME = "AgroSafra"',
                    language="toml",
                )
                st.markdown("**Gmail:**")
                st.code(
                    'SMTP_HOST = "smtp.gmail.com"\n'
                    'SMTP_PORT = "587"\n'
                    'SMTP_USER = "seu@gmail.com"\n'
                    'SMTP_PASSWORD = "sua_app_password"\n'
                    'SMTP_FROM_NAME = "AgroSafra"',
                    language="toml",
                )
                if smtp_erro:
                    st.caption(f"Erro atual: `{smtp_erro}`")

    # ── 2. Destinatários ────────────────────────────────────────────────────
    with col_dest:
        try:
            from src.services.email.recipients import get_recipient_groups, get_executive_recipients
            groups_dest = get_recipient_groups(tenant_id)
            exec_recs = get_executive_recipients(tenant_id)
            total_gestor = sum(len(g.recipients) for g in groups_dest)
            total_exec = len(exec_recs)
            total_dest = total_gestor + total_exec
            if total_dest:
                st.success(
                    f"✅ {total_gestor} gestor(es) · {total_exec} supervisor(es)/admin(s)")
            else:
                st.warning("⚠️ Nenhum destinatário encontrado")
        except Exception as e:
            st.warning(f"Não foi possível carregar destinatários: {e}")

    # ── 2b. Configuração de destinatários ───────────────────────────────────
    with st.expander("👥 Configurar destinatários e tipo de relatório", expanded=False):
        st.caption(
            "**Tipo padrão por role:** gestor → relatório de departamento · "
            "supervisor/admin → relatório executivo consolidado.\n\n"
            "Altere individualmente abaixo para sobrescrever o padrão."
        )
        try:
            from src.services.email.recipients import get_all_users_with_prefs, save_email_pref
            users_prefs = get_all_users_with_prefs(tenant_id)
            if users_prefs:
                role_icons = {"admin": "🔴", "supervisor": "🟣", "gestor": "🟠",
                              "executor": "🟡", "viewer": "⚪"}
                tipo_opts = ["gestor", "executivo", "nenhum"]
                tipo_labels = {
                    "gestor": "📋 Departamento",
                    "executivo": "📊 Executivo",
                    "nenhum": "🚫 Não enviar"}

                changed = {}
                for u in users_prefs:
                    icon = role_icons.get(u["role"], "⚪")
                    label = f"{icon} **{u['nome']}** `{u['role']}` — {u['email']}"
                    override_note = " _(override manual)_" if u["override"] else ""
                    col_u, col_sel = st.columns([3, 2])
                    with col_u:
                        st.markdown(label + override_note)
                    with col_sel:
                        cur_tipo = u["tipo_relatorio"]
                        novo = st.selectbox(
                            "Tipo",
                            options=tipo_opts,
                            index=tipo_opts.index(cur_tipo) if cur_tipo in tipo_opts else 0,
                            format_func=lambda t: tipo_labels.get(
                                t,
                                t),
                            key=f"emailpref_{
                                u['user_id']}",
                            label_visibility="collapsed",
                        )
                        if novo != cur_tipo:
                            changed[u["user_id"]] = novo

                if changed:
                    if st.button(
                        "💾 Salvar preferências",
                            key="save_email_prefs"):
                        from src.services.email.recipients import save_email_pref
                        ok = all(
                            save_email_pref(tenant_id, uid, tipo, ativo=(tipo != "nenhum"))
                            for uid, tipo in changed.items()
                        )
                        if ok:
                            st.success("✅ Preferências salvas!")
                            st.rerun()
                        else:
                            st.error(
                                "Erro ao salvar. Verifique se a tabela `tenant_email_prefs` existe no Supabase.")
                            with st.expander("📋 SQL para criar a tabela", expanded=True):
                                st.code("""
CREATE TABLE tenant_email_prefs (
  tenant_id       uuid NOT NULL,
  user_id         uuid NOT NULL,
  tipo_relatorio  text NOT NULL DEFAULT 'gestor',
  ativo           boolean NOT NULL DEFAULT true,
  updated_at      timestamptz DEFAULT now(),
  PRIMARY KEY (tenant_id, user_id)
);
ALTER TABLE tenant_email_prefs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all" ON tenant_email_prefs
  USING (true) WITH CHECK (true);
""", language="sql")
            else:
                st.info("Nenhum usuário encontrado para este tenant.")
        except Exception as e_prefs:
            st.warning(f"Não foi possível carregar preferências: {e_prefs}")

    st.divider()

    # ── 3. Modo de envio ────────────────────────────────────────────────────
    dry_run = st.toggle(
        "🧪 Modo teste — gerar PDFs sem enviar e-mails",
        value=True,
        key="ntf_email_dry",
        help="Ative para validar a geração dos PDFs sem disparar nenhum e-mail.",
    )

    if dry_run:
        st.info("PDFs serão gerados e validados, mas **nenhum e-mail será enviado**.")
    else:
        if not smtp_ok:
            st.error("Configure o SMTP antes de enviar e-mails reais.")
        else:
            st.warning(
                "⚠️ Modo real — os e-mails **serão enviados** aos responsáveis de cada departamento.")

    # ── 4. Botão de disparo ─────────────────────────────────────────────────
    btn_label = "🧪 Testar geração de PDFs" if dry_run else "📧 Enviar relatórios agora"

    do_send = st.button(
        btn_label,
        type="primary",
        use_container_width=False,
        key="ntf_send_btn",
    )

    # ── 5. Execução ─────────────────────────────────────────────────────────
    if do_send:
        if not dry_run and not smtp_ok:
            st.error(
                "Configure o SMTP no `secrets.toml` antes de enviar e-mails reais.")
            return
        from src.services.email.dispatcher import dispatch_relatorio_semanal
        log_lines: list[str] = []

        with st.spinner("Enviando relatórios…", show_time=True):
            try:
                result = dispatch_relatorio_semanal(
                    tenant_id=tenant_id,
                    revisao_id=revisao_id,
                    dias_travado=dias_travado,
                    dias_sem_update=dias_sem_update,
                    dry_run=dry_run,
                    progress_callback=lambda msg: log_lines.append(msg),
                )
            except Exception as exc:
                st.error(f"Erro inesperado: {exc}")
                return

        if result.failed == 0 and result.sent > 0:
            if dry_run:
                st.success(
                    f"✅ Teste concluído — {
                        result.sent} PDF(s) gerado(s) com sucesso. Nenhum e-mail enviado.")
            else:
                st.success(
                    f"✅ {
                        result.sent} e-mail(s) enviado(s) com sucesso!")
        elif result.sent == 0 and result.skipped > 0:
            st.warning(
                "Nenhum departamento com destinatário válido encontrado.")
        elif result.failed > 0:
            st.warning(f"Concluído com {result.failed} falha(s).")

        if result.errors:
            with st.expander("❌ Erros", expanded=True):
                for err in result.errors:
                    st.error(err)

        if log_lines:
            with st.expander("📋 Log completo", expanded=False):
                st.code("\n".join(log_lines))


@st.fragment
def _fragment_configurar_agendamento(tenant_id: str, is_admin: bool) -> None:
    """Painel de configuração do agendamento automático de e-mail."""
    from src.services.email.email_schedule import (
        DIAS_SEMANA_LABELS, PERIODICIDADE_LABELS, PERIODICIDADE_OPTS,
        ScheduleConfig, load_schedule_config, save_schedule_config,
    )

    st.markdown("### ⏰ Agendamento Automático")

    if not is_admin:
        st.info("Apenas administradores podem configurar o agendamento.")
        return

    with st.spinner("", show_time=False):
        cfg = load_schedule_config(tenant_id)

    # ── Status atual ────────────────────────────────────────────────────────
    col_status, col_prox = st.columns(2)
    with col_status:
        if cfg.ativo:
            st.success(f"✅ Ativo — {cfg.descricao_humana()}")
        else:
            st.warning(f"⏸ Pausado — {cfg.descricao_humana()}")
    with col_prox:
        try:
            proximo = cfg.proximo_disparo_brt().strftime("%d/%m/%Y às %H:%M")
            st.info(f"Próximo disparo: **{proximo}** (Brasília)")
        except Exception:
            st.caption("Configure abaixo para ver o próximo disparo.")

    st.divider()

    # ── Formulário ──────────────────────────────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        ativo = st.toggle(
            "Agendamento ativo",
            value=cfg.ativo,
            key="sch_ativo")
        periodicidade_idx = PERIODICIDADE_OPTS.index(
            cfg.periodicidade) if cfg.periodicidade in PERIODICIDADE_OPTS else 0
        periodicidade = st.selectbox(
            "Periodicidade",
            options=PERIODICIDADE_OPTS,
            index=periodicidade_idx,
            format_func=lambda x: PERIODICIDADE_LABELS.get(x, x),
            key="sch_period",
        )
        dias_travado = st.number_input(
            "Alertar travado há (dias)",
            min_value=1,
            max_value=30,
            value=cfg.dias_travado,
            key="sch_dias_trav")

    with col2:
        hora_envio = st.text_input(
            "Horário (HH:MM — Brasília)",
            value=cfg.hora_envio or "07:00",
            key="sch_hora")

        if periodicidade == "mensal":
            dia_mes = st.number_input(
                "Dia do mês",
                min_value=1,
                max_value=28,
                value=cfg.dia_mes or 1,
                key="sch_dia_mes")
            dia_semana = cfg.dia_semana
        else:
            dia_semana = st.selectbox(
                "Dia da semana",
                options=list(
                    range(7)),
                index=cfg.dia_semana %
                7,
                format_func=lambda i: DIAS_SEMANA_LABELS[i],
                key="sch_dia_sem")
            dia_mes = cfg.dia_mes

        dias_parado = st.number_input(
            "Alertar parado há (dias)",
            min_value=1,
            max_value=30,
            value=cfg.dias_parado,
            key="sch_dias_par")

    # ── Validação e salvar ──────────────────────────────────────────────────
    hora_valida = True
    try:
        hh, mm = hora_envio.split(":")
        assert 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59
    except Exception:
        st.error("Formato de horário inválido. Use HH:MM (ex: 07:00).")
        hora_valida = False

    col_save, col_preview = st.columns([1, 2])
    with col_save:
        if st.button("💾 Salvar", type="primary", use_container_width=True,
                     key="sch_save", disabled=not hora_valida):
            new_cfg = ScheduleConfig(
                tenant_id=tenant_id, id=cfg.id, ativo=ativo,
                periodicidade=periodicidade, dia_semana=int(dia_semana),
                dia_mes=int(dia_mes), hora_envio=hora_envio.strip(),
                dias_travado=int(dias_travado), dias_parado=int(dias_parado),
                revisao_fixa=cfg.revisao_fixa,
            )
            if save_schedule_config(new_cfg):
                st.success("✅ Configuração salva!")
                st.rerun()
            else:
                st.error(
                    "Falha ao salvar. Verifique se a tabela `email_schedule_config` existe no Supabase.")
    with col_preview:
        if hora_valida:
            try:
                preview_cfg = ScheduleConfig(
                    tenant_id=tenant_id,
                    ativo=ativo,
                    periodicidade=periodicidade,
                    dia_semana=int(dia_semana),
                    dia_mes=int(dia_mes),
                    hora_envio=hora_envio.strip(),
                    dias_travado=int(dias_travado),
                    dias_parado=int(dias_parado),
                )
                prox = preview_cfg.proximo_disparo_brt().strftime("%d/%m/%Y às %H:%M")
                st.caption(f"**Prévia:** {preview_cfg.descricao_humana()}")
                st.caption(f"Próximo disparo: {prox} (Brasília)")
            except Exception:
                pass

    with st.expander("📋 Como configurar o scheduler", expanded=False):
        st.markdown(f"""
O `scheduler.py` e o GitHub Actions lêem esta configuração automaticamente do Supabase.

**Secrets necessários no repositório GitHub:**

| Secret | Valor |
|--------|-------|
| `SUPABASE_URL` | URL do Supabase |
| `SUPABASE_SERVICE_ROLE_KEY` | Chave service role |
| `SMTP_HOST` | ex: `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | e-mail remetente |
| `SMTP_PASSWORD` | senha ou App Password |
| `SCHEDULER_TENANT_ID` | `{tenant_id}` |
""")

# ── Ponto de entrada público ────────────────────────────────────────────


def render_notificacoes() -> None:
    _ph("🔔", "Notificações",
        "Alertas proativos: travados, parados, sem início e risco de prazo.")

    tenant_id = current_tenant_id()
    if not tenant_id:
        st.info("Selecione um tenant para ver as notificações.")
        return

    ver = str(st.session_state.get("data_version", "0"))
    revisao_id = get_current_revisao()
    if not revisao_id:
        notice_card(
            "Nenhuma revisão selecionada",
            "Abra uma revisão ativa pela Matriz ou Home para visualizar os alertas operacionais e de prazo.",
            tone="warning",
        )
        return

    # ── Configurações dos thresholds ─────────────────────────────────────────
    with st.expander("⚙️ Configurar thresholds", expanded=False):
        tc1, tc2 = st.columns(2)
        with tc1:
            dias_travado = st.number_input(
                "Alertar travado há (dias)",
                min_value=1,
                max_value=30,
                value=2,
                step=1,
                key="ntf_dias_trav",
                help="Tarefas com status 'travado' há pelo menos X dias.")
        with tc2:
            dias_sem_update = st.number_input(
                "Alertar parado há (dias)",
                min_value=1,
                max_value=30,
                value=5,
                step=1,
                key="ntf_dias_upd",
                help="Tarefas não concluídas sem atualização há X dias.")

    # ── Carregamento ─────────────────────────────────────────────────────────
    with st.spinner("", show_time=False):
        raw = _load_data(tenant_id, revisao_id, ver)

    tarefas = raw["tarefas"]
    revisao = raw["revisao"]

    if not tarefas:
        notice_card(
            "Revisão sem tarefas",
            "A revisão selecionada ainda não possui tarefas sincronizadas para gerar alertas.",
            tone="warning",
        )
        return

    # Filtro de escopo (não-admin vê apenas seus grupos)
    role = current_role()
    is_admin = Role.is_admin(role)
    if not is_admin:
        dep_ids, grp_ids = get_my_scope(tenant_id)
        if grp_ids:
            tarefas = [
                t for t in tarefas
                if (t.get("equipamentos") or {}).get("grupo_id") in grp_ids
            ]

    alertas = _build_alertas(
        tarefas,
        revisao,
        int(dias_travado),
        int(dias_sem_update))

    selection_summary(
        "Parâmetros dos alertas",
        {
            "Revisão": revisao.get("titulo") or "-",
            "Semana": f"{alertas.get('semana_atual', 1)}/{alertas.get('semanas_total', 1)}",
            "Travado há": f"{int(dias_travado)} dia(s)",
            "Sem atualização há": f"{int(dias_sem_update)} dia(s)",
        },
        caption="Os alertas abaixo usam os limites configurados no painel de thresholds.",
    )

    total_alertas = (
        len(alertas["travados"])
        + len(alertas["sem_inicio"])
        + len(alertas["sem_update"])
        + len(alertas["risco_prazo"])
    )
    if total_alertas == 0:
        notice_card(
            "Nenhum alerta crítico encontrado",
            "Com os parâmetros atuais, a revisão não possui equipamentos travados, sem início, sem atualização relevante ou em risco de prazo.",
            tone="success",
        )

    # ── Resumo global ───────────────────────────────────────────────────────
    _fragment_resumo(alertas, revisao)

    st.markdown("---")

    # ── Abas por categoria + resumo por grupo ───────────────────────────────
    tab_trav, tab_sem, tab_par, tab_risc, tab_grupos, tab_export, tab_email = st.tabs([
        f"🚫 Travados ({len(alertas['travados'])})",
        f"⬜ Sem início ({len(alertas['sem_inicio'])})",
        f"⏸ Parados ({len(alertas['sem_update'])})",
        f"⚠️ Risco prazo ({len(alertas['risco_prazo'])})",
        "📊 Por grupo",
        "⬇️ Exportar",
        "📧 Enviar por e-mail",
    ])

    with tab_trav:
        _fragment_travados(alertas["travados"])

    with tab_sem:
        _fragment_sem_inicio(alertas["sem_inicio"])

    with tab_par:
        _fragment_parados(alertas["sem_update"])

    with tab_risc:
        _fragment_risco_prazo(alertas["risco_prazo"])

    with tab_grupos:
        _fragment_resumo_grupos(alertas)

    with tab_export:
        st.markdown("### ⬇️ Exportações")
        st.caption(
            "Baixe os alertas em formato CSV por categoria ou PDF consolidado.")

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**CSV por categoria**")
            _df_download(
                alertas["travados"],
                "Travados",
                "alertas_travados_exp.csv")
            _df_download(
                alertas["sem_inicio"],
                "Sem início",
                "alertas_sem_inicio_exp.csv")
            _df_download(
                alertas["sem_update"],
                "Parados",
                "alertas_parados_exp.csv")
            _df_download(
                alertas["risco_prazo"],
                "Risco prazo",
                "alertas_risco_prazo_exp.csv")
        with col2:
            st.markdown("**PDF consolidado**")
            try:
                import reportlab  # noqa: F401
                pdf_bytes = _build_pdf_alertas(alertas, revisao)
                titulo_rev = (
                    revisao.get("titulo") or "revisao").replace(
                    "/", "-")
                st.download_button(
                    "⬇️ Baixar PDF completo",
                    data=pdf_bytes,
                    file_name=f"alertas_{titulo_rev}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                    type="primary",
                    key="ntf_pdf_dl",
                )
            except ImportError:
                st.info(
                    "Instale `reportlab` no requirements.txt para habilitar exportação em PDF.")

    with tab_email:
        st.markdown("### 📧 Envio de Relatório por E-mail")
        st.caption(
            "Envie manualmente ou configure o agendamento automático por departamento.")
        st.divider()
        _fragment_disparo_manual(tenant_id, revisao_id, is_admin,
                                 int(dias_travado), int(dias_sem_update))
        st.divider()
        _fragment_configurar_agendamento(tenant_id, is_admin)

    # ── Botão de atualizar ──────────────────────────────────────────────────
    if st.button("🔄 Atualizar alertas", key="ntf_refresh"):
        bump_data_version()
        clear_cached_functions(_load_data)
        st.rerun()
