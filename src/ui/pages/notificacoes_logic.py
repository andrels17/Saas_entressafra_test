
"""Lógica e geração de artefatos da página de notificações."""
from __future__ import annotations

import io
import logging
from datetime import datetime

import pandas as pd
import streamlit as st

from src.utils.supabase_helpers import sb_for_user
from src.db.supabase_client import get_supabase_anon


def _sb_from_token(token: str = ""):
    sb = get_supabase_anon()
    if token:
        sb.postgrest.auth(token)
    return sb



log = logging.getLogger(__name__)


def with_fallback(action, default, *, context: str):
    try:
        return action()
    except Exception as exc:  # consultas/integrações externas variam por ambiente
        log.warning("%s: %s", context, exc)
        return default


@st.cache_data(ttl=60, show_spinner=False)
def load_data(tid: str, rev_id: str, ver: str = "0", _token: str = "") -> dict:
    """Carrega todos os dados necessários para os alertas em uma só query."""
    sb = sb_for_user()
    tarefas = with_fallback(
        lambda: (
            sb.table("tarefas_servico")
            .select(
                "id,equipamento_id,servico_id,status,semana,"
                "etapa_d,etapa_r,etapa_m,observacao,"
                "dt_etapa_d,dt_etapa_r,dt_etapa_m,updated_at,"
                "servicos(id,nome,setor_id,setores(nome)),"
                "equipamentos(id,frota,modelo,grupo_id,"
                "equip_grupos(id,nome,departamento_id))"
            )
            .eq("tenant_id", tid)
            .eq("revisao_id", rev_id)
            .execute()
            .data
        ) or [],
        [],
        context=f"Falha ao carregar tarefas de notificações da revisão {rev_id}",
    )

    revisao_rows = with_fallback(
        lambda: (
            sb.table("revisoes")
            .select("id,titulo,data_inicio,semanas_total,status")
            .eq("id", rev_id)
            .limit(1)
            .execute()
            .data
        ),
        [],
        context=f"Falha ao carregar revisão {rev_id} nas notificações",
    )
    revisao = revisao_rows[0] if revisao_rows else {}

    return {"tarefas": tarefas, "revisao": revisao}


def semana_atual(data_inicio_str: str | None, semanas_total: int) -> int:
    if not data_inicio_str:
        return 1
    try:
        inicio = pd.to_datetime(data_inicio_str, utc=True)
        agora = pd.Timestamp.utcnow()
        semana = max(1, int((agora - inicio).days // 7) + 1)
        return min(semana, semanas_total or semana)
    except (TypeError, ValueError):
        log.warning("Data de início inválida para cálculo de semana: %s", data_inicio_str)
        return 1


def dias_desde(ts_str: str | None) -> int | None:
    if not ts_str:
        return None
    try:
        ts = pd.to_datetime(ts_str, utc=True)
        return int((pd.Timestamp.utcnow() - ts).total_seconds() // 86400)
    except (TypeError, ValueError):
        log.warning("Timestamp inválido em notificações: %s", ts_str)
        return None


def build_alertas(tarefas: list[dict], revisao: dict,
                  dias_travado: int, dias_sem_update: int) -> dict:
    """Classifica cada tarefa em categorias de alerta."""
    semana = semana_atual(revisao.get("data_inicio"), revisao.get("semanas_total") or 99)
    semanas_total = revisao.get("semanas_total") or 99

    travados, sem_inicio, sem_update, risco_prazo = [], [], [], []

    eq_tasks: dict[str, list] = {}
    for tarefa in tarefas:
        eq = tarefa.get("equipamentos") or {}
        eid = eq.get("id") or tarefa.get("equipamento_id", "")
        eq_tasks.setdefault(eid, []).append(tarefa)

    for tasks in eq_tasks.values():
        eq = tasks[0].get("equipamentos") or {}
        frota = eq.get("frota") or eq.get("id") or ""
        modelo = eq.get("modelo") or ""
        grupo = (eq.get("equip_grupos") or {}).get("nome") or "—"
        dept_id = (eq.get("equip_grupos") or {}).get("departamento_id")

        total = len(tasks) * 3
        done = sum(
            int(bool(t.get("etapa_d"))) + int(bool(t.get("etapa_r"))) + int(bool(t.get("etapa_m")))
            for t in tasks
        )
        pct = round((done / max(total, 1)) * 100)
        esperado_pct = round((semana / max(semanas_total, 1)) * 100)

        base = {
            "Frota": frota,
            "Modelo": modelo,
            "Grupo": grupo,
            "dept_id": dept_id,
            "% Atual": pct,
            "% Esperado": esperado_pct,
        }

        for tarefa in tasks:
            svc = tarefa.get("servicos") or {}
            setor = (svc.get("setores") or {}).get("nome") or "—"
            svc_nome = svc.get("nome") or "—"
            status = tarefa.get("status") or "pendente"
            updated = (
                tarefa.get("updated_at")
                or tarefa.get("dt_etapa_m")
                or tarefa.get("dt_etapa_r")
                or tarefa.get("dt_etapa_d")
            )
            dias = dias_desde(updated)

            row = {
                **base,
                "Setor": setor,
                "Serviço": svc_nome,
                "Status": status,
                "Obs.": tarefa.get("observacao") or "",
            }

            if status == "travado" and (dias is None or dias >= dias_travado):
                travados.append({**row, "Dias travado": dias if dias is not None else "?"})

            if not tarefa.get("etapa_d") and not tarefa.get("etapa_r") and not tarefa.get("etapa_m"):
                if dias is None or dias >= dias_sem_update:
                    sem_inicio.append({**row, "Dias sem update": dias if dias is not None else "?"})

            if status not in ("concluido", "nao_aplica", "travado"):
                if dias is not None and dias >= dias_sem_update:
                    sem_update.append({**row, "Dias parado": dias})

        if pct < esperado_pct - 15 and pct < 100:
            risco_prazo.append({
                **base,
                "Atraso (p.p.)": esperado_pct - pct,
                "Etapas feitas": done,
                "Etapas total": total,
            })

    return {
        "travados": pd.DataFrame(travados) if travados else pd.DataFrame(),
        "sem_inicio": pd.DataFrame(sem_inicio) if sem_inicio else pd.DataFrame(),
        "sem_update": pd.DataFrame(sem_update) if sem_update else pd.DataFrame(),
        "risco_prazo": pd.DataFrame(risco_prazo) if risco_prazo else pd.DataFrame(),
        "semana_atual": semana,
        "semanas_total": semanas_total,
    }


def resumo_por_grupo(alertas: dict) -> pd.DataFrame:
    """Consolida contagem de alertas por grupo."""
    grupos: dict[str, dict] = {}
    key_map = {
        "travados": "Travados",
        "sem_inicio": "Sem início",
        "sem_update": "Parados",
        "risco_prazo": "Risco prazo",
    }
    for categoria in key_map:
        df = alertas.get(categoria, pd.DataFrame())
        if df.empty or "Grupo" not in df.columns:
            continue
        for grupo, cnt in df["Grupo"].value_counts().items():
            grupos.setdefault(
                str(grupo),
                {"Grupo": str(grupo), "Travados": 0, "Sem início": 0, "Parados": 0, "Risco prazo": 0},
            )
            grupos[str(grupo)][key_map[categoria]] = int(cnt)
    if not grupos:
        return pd.DataFrame()
    resumo = pd.DataFrame(list(grupos.values()))
    resumo["Total alertas"] = resumo[["Travados", "Sem início", "Parados", "Risco prazo"]].sum(axis=1)
    return resumo.sort_values("Total alertas", ascending=False).reset_index(drop=True)


def build_pdf_alertas(alertas: dict, revisao: dict) -> bytes:
    """Gera PDF consolidado de todos os alertas."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_RIGHT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError:
        log.info("ReportLab não disponível para exportação de alertas em PDF.")
        return b""

    sty = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=sty["Heading1"], fontSize=14, leading=18,
                        textColor=colors.HexColor("#111827"), spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=sty["Heading2"], fontSize=11, leading=14,
                        textColor=colors.HexColor("#111827"), spaceBefore=8, spaceAfter=3)
    p = ParagraphStyle("p", parent=sty["BodyText"], fontSize=9, leading=12,
                       textColor=colors.HexColor("#374151"))
    sm = ParagraphStyle("sm", parent=sty["BodyText"], fontSize=8, leading=10,
                        textColor=colors.grey)

    buf = io.BytesIO()
    page = A4
    margin = 1.5 * cm
    pw = page[0] - 2 * margin
    doc = SimpleDocTemplate(buf, pagesize=page, leftMargin=margin, rightMargin=margin,
                            topMargin=margin, bottomMargin=margin)

    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    ts_style = ParagraphStyle("ts", parent=sty["BodyText"], fontSize=8,
                              alignment=TA_RIGHT, textColor=colors.HexColor("#6B7280"))
    header_t = Table(
        [[Paragraph("Relatório de Alertas — Notificações", h1), Paragraph(f"Emitido em<br/>{now_str}", ts_style)]],
        colWidths=[pw - 3.5 * cm, 3.5 * cm],
        rowHeights=[1 * cm],
    )
    header_t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 1.0, colors.HexColor("#E5E7EB")),
    ]))

    meta_lbl = ParagraphStyle("ml", parent=sty["BodyText"], fontSize=8, textColor=colors.HexColor("#6B7280"))
    meta_val = ParagraphStyle("mv", parent=sty["BodyText"], fontSize=10,
                              textColor=colors.HexColor("#111827"), fontName="Helvetica-Bold")
    meta_t = Table([
        [Paragraph("Revisão", meta_lbl), Paragraph("Semana", meta_lbl)],
        [Paragraph(revisao.get("titulo") or "—", meta_val), Paragraph(f'{alertas["semana_atual"]} / {alertas["semanas_total"]}', meta_val)],
    ], colWidths=[pw * 0.6, pw * 0.4])
    meta_t.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 1), (-1, 1), 0.5, colors.HexColor("#E5E7EB")),
    ]))

    n_trav = len(alertas["travados"])
    n_sem = len(alertas["sem_inicio"])
    n_upd = len(alertas["sem_update"])
    n_risc = len(alertas["risco_prazo"])
    kpi_t = Table([[
        Paragraph(f'<font color="#6B7280" size="8">Travados</font><br/><b><font size="16" color="#EF4444">{n_trav}</font></b>', p),
        Paragraph(f'<font color="#6B7280" size="8">Sem início</font><br/><b><font size="16">{n_sem}</font></b>', p),
        Paragraph(f'<font color="#6B7280" size="8">Parados</font><br/><b><font size="16">{n_upd}</font></b>', p),
        Paragraph(f'<font color="#6B7280" size="8">Risco prazo</font><br/><b><font size="16" color="#F59E0B">{n_risc}</font></b>', p),
    ]], colWidths=[pw / 4] * 4, rowHeights=[1.4 * cm])
    kpi_t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
        ("LINEAFTER", (0, 0), (2, 0), 0.5, colors.HexColor("#E5E7EB")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F9FAFB")),
    ]))

    story = [header_t, Spacer(1, 0.3 * cm), meta_t, Spacer(1, 0.4 * cm), kpi_t, Spacer(1, 0.5 * cm)]

    def append_df_table(df: pd.DataFrame, cols_show: list[str], title: str,
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
        cw = []
        for coluna in cols_ok:
            if coluna in ("Frota", "Equipamento"):
                cw.append(pw * 0.18)
            elif coluna in ("Modelo", "Serviço", "Setor", "Obs."):
                cw.append(pw * 0.20)
            else:
                cw.append(pw * 0.12)
        total_w = sum(cw)
        cw = [w * pw / total_w for w in cw]
        tabela = Table(data_rows, colWidths=cw, repeatRows=1)
        tabela.setStyle(TableStyle([
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
        ]))
        story.append(tabela)
        story.append(Spacer(1, 0.4 * cm))

    append_df_table(alertas["travados"], ["Frota", "Modelo", "Grupo", "Setor", "Serviço", "Dias travado", "Obs."],
                    "Travados sem resolução", (colors.HexColor("#7F1D1D"), colors.white))
    append_df_table(alertas["sem_inicio"], ["Frota", "Modelo", "Grupo", "Setor", "Serviço", "Dias sem update"],
                    "Sem nenhum apontamento", (colors.HexColor("#1E3A5F"), colors.white))
    append_df_table(alertas["sem_update"], ["Frota", "Modelo", "Grupo", "Setor", "Serviço", "Status", "Dias parado"],
                    "Parados (sem atualização)", (colors.HexColor("#374151"), colors.white))
    append_df_table(alertas["risco_prazo"], ["Frota", "Modelo", "Grupo", "% Atual", "% Esperado", "Atraso (p.p.)"],
                    "Risco de não concluir no prazo", (colors.HexColor("#78350F"), colors.white))

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawRightString(page[0] - margin, 0.8 * cm, f"Página {canvas.getPageNumber()}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
