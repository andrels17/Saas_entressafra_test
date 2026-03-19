"""Geração de PDF da matriz operacional."""
from __future__ import annotations

import io

import pandas as pd


def _reportlab_available() -> bool:
    try:
        return True
    except Exception:
        return False


def _build_pdf_tables(
    *,
    titulo,
    grupo_nome,
    resumo_df,
        sector_tables) -> bytes:
    from reportlab.lib import colors
    from src.utils.timezone import fmt_brt as _fmt_brt
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        HRFlowable,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    PAGE = landscape(A4)
    LMARGIN = RMARGIN = 1.25 * cm
    TMARGIN = 1.15 * cm
    BMARGIN = 1.20 * cm
    pw = PAGE[0] - LMARGIN - RMARGIN

    sty = getSampleStyleSheet()
    palette = {
        "ink": colors.HexColor("#111827"),
        "muted": colors.HexColor("#6B7280"),
        "soft": colors.HexColor("#374151"),
        "line": colors.HexColor("#E5E7EB"),
        "line_dark": colors.HexColor("#CBD5E1"),
        "panel": colors.HexColor("#F8FAFC"),
        "header": colors.HexColor("#0F172A"),
        "header_2": colors.HexColor("#1E293B"),
        "ok": colors.HexColor("#15803D"),
        "ok_fill": colors.HexColor("#DCFCE7"),
        "warn": colors.HexColor("#D97706"),
        "warn_fill": colors.HexColor("#FEF3C7"),
        "bad": colors.HexColor("#DC2626"),
        "bad_fill": colors.HexColor("#FEE2E2"),
        "empty_fill": colors.HexColor("#F8FAFC"),
    }

    def _pct_color(value: int):
        if value >= 80:
            return palette["ok"]
        if value >= 50:
            return palette["warn"]
        return palette["bad"]

    def _pct_fill(value: int):
        if value >= 80:
            return palette["ok_fill"]
        if value >= 50:
            return palette["warn_fill"]
        return palette["bad_fill"]

    def _int_pct(value) -> int:
        try:
            return int(round(float(value or 0)))
        except Exception:
            return 0

    def _safe_int(value) -> int:
        try:
            return int(value or 0)
        except Exception:
            return 0

    title_style = ParagraphStyle(
        "pdf_title",
        parent=sty["Heading1"],
        fontSize=16,
        leading=18,
        alignment=TA_LEFT,
        textColor=palette["ink"],
        fontName="Helvetica-Bold",
        spaceAfter=0,
    )
    section_style = ParagraphStyle(
        "pdf_section",
        parent=sty["Heading2"],
        fontSize=11.2,
        leading=13,
        alignment=TA_LEFT,
        textColor=palette["ink"],
        fontName="Helvetica-Bold",
        spaceBefore=0,
        spaceAfter=1,
    )
    body_style = ParagraphStyle(
        "pdf_body",
        parent=sty["BodyText"],
        fontSize=8.3,
        leading=10,
        alignment=TA_LEFT,
        textColor=palette["soft"],
    )
    small_style = ParagraphStyle(
        "pdf_small",
        parent=sty["BodyText"],
        fontSize=7.3,
        leading=8.5,
        alignment=TA_LEFT,
        textColor=palette["muted"],
    )
    meta_label = ParagraphStyle(
        "meta_label",
        parent=small_style,
        fontSize=7.5,
        textColor=palette["muted"],
    )
    meta_value = ParagraphStyle(
        "meta_value",
        parent=body_style,
        fontSize=9.2,
        leading=10.8,
        textColor=palette["ink"],
        fontName="Helvetica-Bold",
    )
    card_label = ParagraphStyle(
        "card_label",
        parent=small_style,
        fontSize=7.8,
        leading=9.2,
        alignment=TA_CENTER,
        textColor=palette["muted"],
    )
    card_value = ParagraphStyle(
        "card_value",
        parent=body_style,
        fontSize=14,
        leading=15,
        alignment=TA_CENTER,
        textColor=palette["ink"],
        fontName="Helvetica-Bold",
    )
    issued_style = ParagraphStyle(
        "issued_style",
        parent=small_style,
        alignment=TA_RIGHT,
        fontSize=7.8,
        leading=9.5,
        textColor=palette["ink"],
    )
    sector_meta_style = ParagraphStyle(
        "sector_meta_style",
        parent=body_style,
        fontSize=8.5,
        leading=9.5,
        textColor=palette["soft"],
    )
    head_top = ParagraphStyle(
        "head_top",
        parent=small_style,
        alignment=TA_CENTER,
        fontSize=7.7,
        leading=8.4,
        textColor=colors.white,
        fontName="Helvetica-Bold",
    )
    head_sub = ParagraphStyle(
        "head_sub",
        parent=small_style,
        alignment=TA_CENTER,
        fontSize=6.8,
        leading=7.4,
        textColor=colors.HexColor("#CBD5E1"),
        fontName="Helvetica-Bold",
    )
    cell_left = ParagraphStyle(
        "cell_left",
        parent=body_style,
        fontSize=8,
        leading=8.9,
        alignment=TA_LEFT,
        textColor=palette["ink"],
    )
    cell_center = ParagraphStyle(
        "cell_center",
        parent=body_style,
        fontSize=7.8,
        leading=8.6,
        alignment=TA_CENTER,
        textColor=palette["soft"],
    )

    def _base_left_table_style(*, header_rows=0, zebra_from=1):
        cmds = [
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.32, palette["line"]),
            ("BOX", (0, 0), (-1, -1), 0.45, palette["line_dark"]),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]
        if header_rows:
            cmds.extend([
                ("BACKGROUND", (0, 0), (-1, 0), palette["header"]),
                ("TEXTCOLOR", (0, 0), (-1, header_rows - 1), colors.white),
                ("FONTNAME", (0, 0), (-1, header_rows - 1), "Helvetica-Bold"),
            ])
        cmds.append(("ROWBACKGROUNDS", (0, zebra_from),
                    (-1, -1), [colors.white, palette["panel"]]))
        return cmds

    def _kpi_card(title: str, value_markup: str):
        card = Table(
            [[Paragraph(title, card_label)], [Paragraph(value_markup, card_value)]],
            colWidths=[pw / 4.0],
            rowHeights=[0.50 * cm, 0.80 * cm],
        )
        card.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.white),
            ("BOX", (0, 0), (-1, -1), 0.55, palette["line"]),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        return card

    def _summary_table(df: pd.DataFrame):
        cols = ["Equipamento", "Concluidos", "Total", "%"]
        if not isinstance(
                df, pd.DataFrame) or not all(
                c in df.columns for c in cols):
            return Paragraph("Sem dados.", small_style)

        view = df[cols].copy()
        view["Concluidos"] = view["Concluidos"].map(_safe_int)
        view["Total"] = view["Total"].map(_safe_int)
        view["%"] = view["%"].map(_int_pct)
        view = view.sort_values(["%", "Concluidos", "Equipamento"], ascending=[
                                False, False, True]).reset_index(drop=True)

        rows = [[
            Paragraph("<b>Equipamento</b>", cell_left),
            Paragraph("<b>Concluídos</b>", cell_left),
            Paragraph("<b>Total</b>", cell_left),
            Paragraph("<b>%</b>", cell_left),
        ]]
        for _, row in view.iterrows():
            pct = _int_pct(row["%"])
            rows.append([
                Paragraph(str(row["Equipamento"]), cell_left),
                Paragraph(str(_safe_int(row["Concluidos"])), cell_left),
                Paragraph(str(_safe_int(row["Total"])), cell_left),
                Paragraph(f"<b>{pct}%</b>", cell_left),
            ])

        table = Table(
            rows,
            colWidths=[
                pw * 0.57,
                pw * 0.14,
                pw * 0.11,
                pw * 0.18],
            repeatRows=1)
        table.hAlign = "LEFT"
        style_cmds = _base_left_table_style(header_rows=1, zebra_from=1) + [
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ]
        for row_idx, pct in enumerate(view["%"].tolist(), start=1):
            style_cmds.extend([
                ("BACKGROUND", (3, row_idx), (3, row_idx), _pct_fill(_int_pct(pct))),
                ("TEXTCOLOR", (3, row_idx), (3, row_idx), _pct_color(_int_pct(pct))),
            ])
        table.setStyle(TableStyle(style_cmds))
        return table

    def _split_service_columns(df: pd.DataFrame):
        svc_cols = [
            c for c in df.columns if c not in (
                "Equipamento", "%", "Status")]
        groups = []
        order_map = {"D": 0, "R": 1, "M": 2}
        by_service = {}
        service_order = []
        for col in svc_cols:
            label = str(col)
            service_name = label
            suffix = None
            try:
                left, right = label.rsplit(" ", 1)
                if right in order_map:
                    service_name, suffix = left, right
            except Exception:
                pass  # ignorado — operação opcional
            if service_name not in by_service:
                by_service[service_name] = {
                    "name": service_name, "cols": [
                        None, None, None], "extras": []}
                service_order.append(service_name)
            if suffix is None:
                by_service[service_name]["extras"].append(col)
            else:
                by_service[service_name]["cols"][order_map[suffix]] = col
        for name in service_order:
            item = by_service[name]
            ordered = [c for c in item["cols"] if c] or item["extras"]
            groups.append((item["name"], ordered))
        return groups

    def _cell_heat_style(raw: str):
        val = str(raw or "").strip().upper()
        if val == "OK":
            return palette["ok_fill"], palette["ok"], "OK"
        if val in {"!", "PEND", "PENDENTE", "NOK", "NÃO", "NAO", "X"}:
            return palette["bad_fill"], palette["bad"], val
        return palette["empty_fill"], palette["muted"], ""

    def _build_sector_block(df: pd.DataFrame):
        groups = _split_service_columns(df)
        if not groups:
            return [Paragraph("Sem dados deste setor.", small_style)]

        equip_w = 5.2 * cm
        trio_w = 0.88 * cm
        max_groups = max(1, min(len(groups), int(
            (pw - equip_w) // (trio_w * 3)) or 1))
        chunks = [groups[i:i + max_groups]
                  for i in range(0, len(groups), max_groups)]
        blocks = []

        for chunk_idx, chunk in enumerate(chunks, start=1):
            data = []
            top = [Paragraph("<b>Equipamento</b>", head_top)]
            sub = [""]
            spans = [(0, 0, 0, 1)]
            cols_meta = [("Equipamento", None)]
            separators = []
            cur_col = 1

            for service_name, ordered_cols in chunk:
                normalized = list(ordered_cols)
                if len(normalized) == 3:
                    top.extend(
                        [Paragraph(f"<b>{service_name}</b>", head_top), "", ""])
                    sub.extend([
                        Paragraph("<b>D</b>", head_sub),
                        Paragraph("<b>R</b>", head_sub),
                        Paragraph("<b>M</b>", head_sub),
                    ])
                    spans.append((cur_col, 0, cur_col + 2, 0))
                    separators.append(cur_col + 2)
                    for col_name in normalized:
                        cols_meta.append((col_name, True))
                    cur_col += 3
                else:
                    top.append(Paragraph(f"<b>{service_name}</b>", head_top))
                    sub.append("")
                    for col_name in normalized:
                        cols_meta.append((col_name, True))
                    if len(normalized) == 1:
                        spans.append((cur_col, 0, cur_col, 1))
                    cur_col += len(normalized)

            data.extend([top, sub])
            view_cols = [name for name, _ in cols_meta]
            view = df[view_cols].copy().fillna("")

            for _, src in view.iterrows():
                row = [Paragraph(str(src["Equipamento"]), cell_left)]
                for col_name, _ in cols_meta[1:]:
                    _, _, text = _cell_heat_style(src[col_name])
                    row.append(Paragraph(text, cell_center))
                data.append(row)

            remaining = pw - equip_w
            matrix_cols = len(cols_meta) - 1
            matrix_w = max(
                0.82 * cm, min(0.94 * cm, remaining / max(matrix_cols, 1)))
            col_widths = [equip_w] + [matrix_w] * matrix_cols
            table = Table(data, colWidths=col_widths, repeatRows=2)
            table.hAlign = "LEFT"

            style_cmds = _base_left_table_style(header_rows=2, zebra_from=2) + [
                ("BACKGROUND", (0, 1), (-1, 1), palette["header_2"]),
                ("TEXTCOLOR", (0, 1), (-1, 1), colors.HexColor("#CBD5E1")),
                ("ALIGN", (0, 0), (-1, 1), "CENTER"),
                ("ALIGN", (0, 2), (0, -1), "LEFT"),
                ("ALIGN", (1, 2), (-1, -1), "CENTER"),
                ("SPAN", (0, 0), (0, 1)),
                ("LEFTPADDING", (1, 2), (-1, -1), 0),
                ("RIGHTPADDING", (1, 2), (-1, -1), 0),
                ("TOPPADDING", (1, 2), (-1, -1), 1),
                ("BOTTOMPADDING", (1, 2), (-1, -1), 1),
            ]
            for c1, r1, c2, r2 in spans[1:]:
                style_cmds.append(("SPAN", (c1, r1), (c2, r2)))
            for col in separators[:-1]:
                style_cmds.append(
                    ("LINEAFTER", (col, 0), (col, -1), 0.7, colors.HexColor("#94A3B8")))

            for row_i in range(2, len(data)):
                for col_i in range(1, len(cols_meta)):
                    bg, fg, text = _cell_heat_style(
                        view.iloc[row_i - 2, col_i])
                    style_cmds.extend([
                        ("BACKGROUND", (col_i, row_i), (col_i, row_i), bg),
                        ("TEXTCOLOR", (col_i, row_i), (col_i, row_i), fg),
                    ])
                    if text:
                        style_cmds.extend([
                            ("FONTNAME", (col_i, row_i), (col_i, row_i), "Helvetica-Bold"),
                            ("BOX", (col_i, row_i), (col_i, row_i), 0.35, fg),
                        ])

            table.setStyle(TableStyle(style_cmds))
            if len(chunks) > 1:
                blocks.append(
                    Paragraph(f"Bloco {chunk_idx}/{len(chunks)}", small_style))
                blocks.append(Spacer(1, 0.10 * cm))
            blocks.append(table)
            if chunk_idx < len(chunks):
                blocks.append(Spacer(1, 0.22 * cm))
        return blocks

    resumo_cols = ["Equipamento", "Concluidos", "Total", "%"]
    if isinstance(resumo_df, pd.DataFrame) and all(
            c in resumo_df.columns for c in resumo_cols):
        rv = resumo_df[resumo_cols].copy()
        rv["Concluidos"] = rv["Concluidos"].map(_safe_int)
        rv["Total"] = rv["Total"].map(_safe_int)
        rv["%"] = rv["%"].map(_int_pct)
    else:
        rv = pd.DataFrame(columns=resumo_cols)

    total_eq = len(rv)
    eq_100 = int((rv["%"] >= 100).sum()) if not rv.empty else 0
    avg_pct = int(round(rv["%"].mean())) if not rv.empty else 0
    eq_zero = int((rv["%"] <= 0).sum()) if not rv.empty else 0
    emitido = _fmt_brt("%d/%m/%Y %H:%M")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=PAGE,
        leftMargin=LMARGIN,
        rightMargin=RMARGIN,
        topMargin=TMARGIN,
        bottomMargin=BMARGIN,
    )

    story = []

    header = Table(
        [
            [
                Paragraph(
                    "Relatório Operacional — Matriz",
                    title_style),
                Paragraph(
                    f'<font color="#6B7280">Data de emissão</font><br/><b>{emitido}</b>',
                    issued_style),
            ]],
        colWidths=[
            pw *
            0.76,
            pw *
            0.24],
    )
    header.hAlign = "LEFT"
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(header)
    story.append(
        HRFlowable(
            width="100%",
            thickness=0.75,
            color=palette["line"],
            spaceAfter=5,
            spaceBefore=1))

    meta = Table(
        [
            [
                Paragraph(
                    "Revisão", meta_label), Paragraph(
                    "Grupo", meta_label)], [
                        Paragraph(
                            titulo or "—", meta_value), Paragraph(
                                grupo_nome or "—", meta_value)], ], colWidths=[
                                    pw * 0.38, pw * 0.62], )
    meta.hAlign = "LEFT"
    meta.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 1), (-1, 1), 0.45, palette["line"]),
    ]))
    story.append(meta)
    story.append(Spacer(1, 0.24 * cm))

    cards = Table([[_kpi_card("Equipamentos",
                              str(total_eq)),
                    _kpi_card("Concluídos (100%)",
                  f'<font color="#16A34A">{eq_100}</font>'),
                    _kpi_card("Progresso médio",
                  f"{avg_pct}%"),
        _kpi_card("Sem início (0%)",
                  f'<font color="#EF4444">{eq_zero}</font>'),
    ]],
        colWidths=[pw / 4.0] * 4,
    )
    cards.hAlign = "LEFT"
    cards.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(cards)
    story.append(Spacer(1, 0.24 * cm))

    story.append(Paragraph("Resumo por equipamento", section_style))
    story.append(Spacer(1, 0.05 * cm))
    story.append(_summary_table(rv))

    for sector_name, sector_df in sector_tables:
        story.append(PageBreak())
        service_cols = [
            c for c in sector_df.columns if c not in (
                "Equipamento", "%", "Status")]
        ok_count = int(
            (sector_df[service_cols] == "OK").sum().sum()) if service_cols else 0
        total_cells = int(
            len(sector_df) *
            len(service_cols)) if service_cols else 0
        pct_general = int(
            round((ok_count / max(total_cells, 1)) * 100)) if total_cells else 0

        story.append(
            Paragraph(
                f"Detalhamento por setor — {sector_name}",
                section_style))
        story.append(
            Paragraph(
                f"Geral: <b>{pct_general}%</b> | Concluídos: <b>{ok_count}/{total_cells}</b>",
                sector_meta_style))
        story.append(Spacer(1, 0.16 * cm))
        for block in _build_sector_block(sector_df):
            story.append(block)

    def _footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(palette["muted"])
        canvas.drawString(
            LMARGIN,
            0.58 * cm,
            "D = desmontou   R = revisou   M = montou")
        canvas.drawRightString(
            PAGE[0] - RMARGIN,
            0.58 * cm,
            f"Página {
                canvas.getPageNumber()}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
