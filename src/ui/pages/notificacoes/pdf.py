"""Geração do PDF consolidado de alertas."""
from __future__ import annotations

import io
from datetime import datetime


def build_pdf_alertas(alertas: dict, revisao: dict) -> bytes:
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
    h2 = ParagraphStyle("h2", parent=sty["Heading2"], fontSize=11, leading=14,
                        textColor=colors.HexColor("#111827"), spaceBefore=8, spaceAfter=3)
    p  = ParagraphStyle("p",  parent=sty["BodyText"], fontSize=9,  leading=12,
                        textColor=colors.HexColor("#374151"))
    sm = ParagraphStyle("sm", parent=sty["BodyText"], fontSize=8,  leading=10,
                        textColor=colors.grey)

    buf = io.BytesIO()
    PAGE, MARGIN = A4, 1.5 * cm
    pw = PAGE[0] - 2 * MARGIN
    doc = SimpleDocTemplate(buf, pagesize=PAGE,
                            leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=MARGIN, bottomMargin=MARGIN)

    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    ts_style = ParagraphStyle("ts", parent=sty["BodyText"], fontSize=8,
                               alignment=TA_RIGHT, textColor=colors.HexColor("#6B7280"))

    header_t = Table(
        [[Paragraph("Relatório de Alertas — Notificações", h1),
          Paragraph(f"Emitido em<br/>{now_str}", ts_style)]],
        colWidths=[pw - 3.5 * cm, 3.5 * cm], rowHeights=[1 * cm],
    )
    header_t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",  (1, 0), (1, 0),  "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 1.0, colors.HexColor("#E5E7EB")),
    ]))

    meta_lbl = ParagraphStyle("ml", parent=sty["BodyText"], fontSize=8,
                               textColor=colors.HexColor("#6B7280"))
    meta_val = ParagraphStyle("mv", parent=sty["BodyText"], fontSize=10,
                               textColor=colors.HexColor("#111827"), fontName="Helvetica-Bold")
    meta_t = Table(
        [[Paragraph("Revisão", meta_lbl), Paragraph("Semana", meta_lbl)],
         [Paragraph(revisao.get("titulo") or "—", meta_val),
          Paragraph(f'{alertas["semana_atual"]} / {alertas["semanas_total"]}', meta_val)]],
        colWidths=[pw * 0.6, pw * 0.4],
    )
    meta_t.setStyle(TableStyle([
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 1), (-1, 1), 0.5, colors.HexColor("#E5E7EB")),
    ]))

    n_trav = len(alertas["travados"])
    n_sem  = len(alertas["sem_inicio"])
    n_upd  = len(alertas["sem_update"])
    n_risc = len(alertas["risco_prazo"])
    kpi_t = Table(
        [[Paragraph(f'<font color="#6B7280" size="8">Travados</font><br/>'
                    f'<b><font size="16" color="#EF4444">{n_trav}</font></b>', p),
          Paragraph(f'<font color="#6B7280" size="8">Sem início</font><br/>'
                    f'<b><font size="16">{n_sem}</font></b>', p),
          Paragraph(f'<font color="#6B7280" size="8">Parados</font><br/>'
                    f'<b><font size="16">{n_upd}</font></b>', p),
          Paragraph(f'<font color="#6B7280" size="8">Risco prazo</font><br/>'
                    f'<b><font size="16" color="#F59E0B">{n_risc}</font></b>', p)]],
        colWidths=[pw / 4] * 4, rowHeights=[1.4 * cm],
    )
    kpi_t.setStyle(TableStyle([
        ("BOX",       (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
        ("LINEAFTER", (0, 0), (2, 0),   0.5, colors.HexColor("#E5E7EB")),
        ("VALIGN",    (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",     (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor("#F9FAFB")),
    ]))

    story = [header_t, Spacer(1, 0.3 * cm), meta_t, Spacer(1, 0.4 * cm),
             kpi_t, Spacer(1, 0.5 * cm)]

    def _df_table(df, cols_show, title, accent=(colors.HexColor("#111827"), colors.white)):
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
        for c in cols_ok:
            if c in ("Frota", "Equipamento"):      cw.append(pw * 0.18)
            elif c in ("Modelo", "Serviço", "Setor", "Obs."): cw.append(pw * 0.20)
            else:                                  cw.append(pw * 0.12)
        total_w = sum(cw)
        cw = [w * pw / total_w for w in cw]
        t = Table(data_rows, colWidths=cw, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), accent[0]),
            ("TEXTCOLOR",  (0, 0), (-1, 0), accent[1]),
            ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",   (0, 0), (-1, 0), 8),
            ("ALIGN",      (0, 0), (-1, 0), "CENTER"),
            ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
            ("FONTSIZE",   (0, 1), (-1, -1), 7.5),
            ("GRID",       (0, 0), (-1, -1), 0.25, colors.lightgrey),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING",   (0, 0), (-1, -1), 3),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F9FAFB")]),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#374151")),
        ]))
        story.append(t)
        story.append(Spacer(1, 0.4 * cm))

    _df_table(alertas["travados"],   ["Frota", "Modelo", "Grupo", "Setor", "Serviço", "Dias travado", "Obs."],
              "Travados sem resolução", (colors.HexColor("#7F1D1D"), colors.white))
    _df_table(alertas["sem_inicio"], ["Frota", "Modelo", "Grupo", "Setor", "Serviço", "Dias sem update"],
              "Sem nenhum apontamento", (colors.HexColor("#1E3A5F"), colors.white))
    _df_table(alertas["sem_update"], ["Frota", "Modelo", "Grupo", "Setor", "Serviço", "Status", "Dias parado"],
              "Parados (sem atualização)", (colors.HexColor("#374151"), colors.white))
    _df_table(alertas["risco_prazo"], ["Frota", "Modelo", "Grupo", "% Atual", "% Esperado", "Atraso (p.p.)"],
              "Risco de não concluir no prazo", (colors.HexColor("#78350F"), colors.white))

    def _footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawRightString(PAGE[0] - MARGIN, 0.8 * cm, f"Página {canvas.getPageNumber()}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
