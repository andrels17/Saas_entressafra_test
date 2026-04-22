from __future__ import annotations

import io

import pandas as pd

from src.ui.pages.matriz_runtime import risk_color as _risk_color


def _df_to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def _compute_setor_ok_counts(eqs, setor_to_services, task_map):
    rows = []
    for setor, services in setor_to_services.items():
        svc_ids = [s.get("id") for s in services if s.get("id")]
        if not svc_ids:
            continue
        total_per = len(svc_ids) * 3
        ok_eq = pct_sum = 0
        for e in eqs:
            done = sum(
                int(bool((task_map.get((e["id"], sid)) or {}).get(f)))
                for sid in svc_ids
                for f in ("etapa_d", "etapa_r", "etapa_m")
            )
            pct_sum += round((done / max(total_per, 1)) * 100)
            if done >= total_per:
                ok_eq += 1
        rows.append(
            {
                "setor": setor,
                "ok_eq": ok_eq,
                "total_eq": len(eqs),
                "pct_med": round(pct_sum / max(len(eqs), 1)),
            }
        )
    rows.sort(key=lambda r: (r["ok_eq"] / max(r["total_eq"], 1), r["pct_med"]))
    return rows


# Melhoria 6: definida fora do loop
def _style_heatmap(df_: pd.DataFrame) -> pd.DataFrame:
    s = pd.DataFrame("", index=df_.index, columns=df_.columns)
    for col in df_.columns:
        if col in ("Status", "%", "Equipamento"):
            continue
        s.loc[df_[col] == "OK", col] = "background-color:rgba(46,204,113,.18);"
        s.loc[df_[col] == "!", col] = "background-color:rgba(231,76,60,.20);"
    return s


def _reportlab_available() -> bool:
    try:
        return True
    except Exception:
        return False


def _compute_sector_progress(sector_tables) -> dict[str, int]:
    result: dict[str, int] = {}
    for setor_nome, df in sector_tables or []:
        if not isinstance(df, pd.DataFrame) or df.empty:
            result[str(setor_nome)] = 0
            continue

        service_cols = [c for c in df.columns if c not in ("Equipamento", "%", "Status")]
        total = len(df) * len(service_cols)
        ok = int((df[service_cols] == "OK").sum().sum()) if service_cols else 0
        result[str(setor_nome)] = int(round((ok / max(total, 1)) * 100)) if total else 0
    return result


def _merge_sector_tables(sector_tables):
    if not sector_tables:
        return pd.DataFrame(), []

    frames = []
    sector_groups = []
    seen_columns: set[str] = set()

    for setor_nome, df in sector_tables:
        if not isinstance(df, pd.DataFrame) or df.empty or "Equipamento" not in df.columns:
            continue

        work = df.copy()
        local_cols = ["Equipamento"]
        service_groups = []
        order_map = {"D": 0, "R": 1, "M": 2}
        by_service: dict[str, dict] = {}
        service_order: list[str] = []

        for col in work.columns:
            if col in ("Equipamento", "%", "Status"):
                continue

            raw = str(col)
            service_name = raw
            suffix = None
            try:
                left, right = raw.rsplit(" ", 1)
                if right in order_map:
                    service_name, suffix = left, right
            except Exception:
                pass

            canonical = f"{setor_nome}|||{raw}"
            idx = 2
            while canonical in seen_columns:
                canonical = f"{setor_nome}|||{raw}__{idx}"
                idx += 1
            seen_columns.add(canonical)

            work = work.rename(columns={col: canonical})
            local_cols.append(canonical)

            if service_name not in by_service:
                by_service[service_name] = {
                    "service": service_name,
                    "triplet": [None, None, None],
                    "extras": [],
                }
                service_order.append(service_name)

            if suffix is None:
                by_service[service_name]["extras"].append(canonical)
            else:
                by_service[service_name]["triplet"][order_map[suffix]] = canonical

        for service_name in service_order:
            item = by_service[service_name]
            ordered_cols = [c for c in item["triplet"] if c] or item["extras"]
            if ordered_cols:
                service_groups.append({"service": service_name, "columns": ordered_cols})

        if service_groups:
            sector_groups.append({"sector": str(setor_nome), "services": service_groups})
            frames.append(work[local_cols])

    if not frames:
        return pd.DataFrame(), []

    base = frames[0]
    for frame in frames[1:]:
        base = base.merge(frame, on="Equipamento", how="outer")

    return base.fillna(""), sector_groups


def _build_pdf_tables(*, titulo, grupo_nome, resumo_df, sector_tables, semana_revisao=None, tarefas_servico_df=None, revisao_id=None, semana_impressa=None) -> bytes:
    from reportlab.lib import colors
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
    from src.utils.timezone import fmt_brt as _fmt_brt

    PAGE = landscape(A4)
    LMARGIN = RMARGIN = 0.6 * cm
    TMARGIN = 0.6 * cm
    BMARGIN = 0.7 * cm
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
        "header_3": colors.HexColor("#334155"),
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
        fontSize=6.6,
        leading=7.0,
        textColor=colors.white,
        fontName="Helvetica-Bold",
    )
    head_sub = ParagraphStyle(
        "head_sub",
        parent=small_style,
        alignment=TA_CENTER,
        fontSize=6.0,
        leading=7.3,
        textColor=colors.HexColor("#CBD5E1"),
        fontName="Helvetica-Bold",
    )
    head_stage = ParagraphStyle(
        "head_stage",
        parent=small_style,
        alignment=TA_CENTER,
        fontSize=5.8,
        leading=6.9,
        textColor=colors.HexColor("#E5E7EB"),
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
        fontSize=7.6,
        leading=8.4,
        alignment=TA_CENTER,
        textColor=palette["soft"],
    )

    summary_head = ParagraphStyle(
        "summary_head",
        parent=small_style,
        fontSize=8,
        leading=9,
        alignment=TA_LEFT,
        textColor=colors.white,
        fontName="Helvetica-Bold",
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
            cmds.extend(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), palette["header"]),
                    ("TEXTCOLOR", (0, 0), (-1, header_rows - 1), colors.white),
                    ("FONTNAME", (0, 0), (-1, header_rows - 1), "Helvetica-Bold"),
                ]
            )
        cmds.append(("ROWBACKGROUNDS", (0, zebra_from), (-1, -1), [colors.white, palette["panel"]]))
        return cmds

    def _kpi_card(title: str, value_markup: str):
        card = Table(
            [[Paragraph(title, card_label)], [Paragraph(value_markup, card_value)]],
            colWidths=[pw / 4.0],
            rowHeights=[0.50 * cm, 0.80 * cm],
        )
        card.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                    ("BOX", (0, 0), (-1, -1), 0.55, palette["line"]),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        return card

    def _summary_table(df: pd.DataFrame):
        cols = ["Equipamento", "Concluidos", "Total", "%"]
        if not isinstance(df, pd.DataFrame) or not all(c in df.columns for c in cols):
            return Paragraph("Sem dados.", small_style)

        view = df[cols].copy()
        view["Concluidos"] = view["Concluidos"].map(_safe_int)
        view["Total"] = view["Total"].map(_safe_int)
        view["%"] = view["%"].map(_int_pct)
        view = view.sort_values(["%", "Concluidos", "Equipamento"], ascending=[False, False, True]).reset_index(drop=True)

        rows = [[
            Paragraph("Equipamento", summary_head),
            Paragraph("Concluídos", summary_head),
            Paragraph("Total", summary_head),
            Paragraph("%", summary_head),
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
            colWidths=[pw * 0.57, pw * 0.14, pw * 0.11, pw * 0.18],
            repeatRows=1,
        )
        table.hAlign = "LEFT"
        style_cmds = _base_left_table_style(header_rows=1, zebra_from=1) + [("ALIGN", (0, 0), (-1, -1), "LEFT")]
        for row_idx, pct in enumerate(view["%"].tolist(), start=1):
            style_cmds.extend(
                [
                    ("BACKGROUND", (3, row_idx), (3, row_idx), _pct_fill(_int_pct(pct))),
                    ("TEXTCOLOR", (3, row_idx), (3, row_idx), _pct_color(_int_pct(pct))),
                ]
            )
        table.setStyle(TableStyle(style_cmds))
        return table

    def _cell_heat_style(raw: str):
        val = str(raw or "").strip().upper()
        if val == "OK":
            return palette["ok_fill"], palette["ok"], "OK"
        if val in {"!", "PEND", "PENDENTE", "NOK", "NÃO", "NAO", "X"}:
            return palette["bad_fill"], palette["bad"], val
        return palette["empty_fill"], palette["muted"], ""

    def _build_consolidated_blocks(df: pd.DataFrame, sector_groups, sector_pct: dict[str, int]):
        if not isinstance(df, pd.DataFrame) or df.empty or not sector_groups:
            return [Paragraph("Sem dados desta matriz.", small_style)]

        equip_w = 3.8 * cm
        stage_pref_w = 0.80 * cm
        stage_min_w = 0.66 * cm
        usable_matrix_w = max(pw - equip_w, stage_min_w * 3)
        max_matrix_cols = max(3, int(usable_matrix_w // stage_min_w))

        chunks = []
        current_chunk = []
        current_cols = 0
        for sector in sector_groups:
            sector_chunk = {"sector": sector["sector"], "services": []}
            for service in sector["services"]:
                service_width = max(1, len(service["columns"]))
                if current_cols > 0 and current_cols + service_width > max_matrix_cols:
                    if sector_chunk["services"]:
                        current_chunk.append(sector_chunk)
                    if current_chunk:
                        chunks.append(current_chunk)
                    current_chunk = []
                    current_cols = 0
                    sector_chunk = {"sector": sector["sector"], "services": []}

                sector_chunk["services"].append(service)
                current_cols += service_width

            if sector_chunk["services"]:
                current_chunk.append(sector_chunk)

        if current_chunk:
            chunks.append(current_chunk)

        blocks = []
        for chunk_idx, chunk in enumerate(chunks, start=1):
            data = []
            row_setor = [Paragraph("<b>Equipamento</b>", head_top)]
            row_servico = [""]
            row_etapa = [""]
            cols_meta = ["Equipamento"]
            spans = [(0, 0, 0, 2)]
            separators = []
            cur_col = 1

            for sector in chunk:
                sector_start = cur_col
                pct = sector_pct.get(sector["sector"], 0)

                for service in sector["services"]:
                    service_cols = list(service["columns"])
                    width = len(service_cols)
                    row_setor.extend([""] * width)
                    row_servico.extend([Paragraph(f"<b>{service['service']}</b>", head_sub)] + [""] * (width - 1))
                    stage_labels = []
                    if width == 3:
                        stage_labels = ["D", "R", "M"]
                    else:
                        for idx in range(width):
                            stage_labels.append(str(idx + 1))
                    row_etapa.extend([Paragraph(f"<b>{lbl}</b>", head_stage) for lbl in stage_labels])
                    cols_meta.extend(service_cols)
                    if width > 1:
                        spans.append((cur_col, 1, cur_col + width - 1, 1))
                    cur_col += width

                sector_end = cur_col - 1
                if sector_end >= sector_start:
                    row_setor[sector_start:sector_start] = [Paragraph(f"<b>{sector['sector']}</b><br/><font size='6'>{pct}%</font>", head_top)]
                    del row_setor[sector_start + 1: sector_end + 2]
                    spans.append((sector_start, 0, sector_end, 0))
                    separators.append(sector_end)

            # Rastreia onde cada serviço começa e termina (para separadores e zebra)
            # service_boundaries: lista de (col_start, col_end) — índices 1-based em cols_meta
            service_boundaries: list[tuple[int, int]] = []
            _svc_cur = 1
            for sector in chunk:
                for service in sector["services"]:
                    _w = max(1, len(service["columns"]))
                    service_boundaries.append((_svc_cur, _svc_cur + _w - 1))
                    _svc_cur += _w

            # Cores alternadas leves para distinguir serviços no header row_servico
            _svc_header_colors = [
                colors.HexColor("#1E293B"),  # par  — mesmo tom do header_2
                colors.HexColor("#2D3F55"),  # ímpar — ligeiramente mais claro
            ]

            data.extend([row_setor, row_servico, row_etapa])
            view = df[cols_meta].copy().fillna("")

            for _, src in view.iterrows():
                row = [Paragraph(str(src["Equipamento"]), cell_left)]
                for col_name in cols_meta[1:]:
                    _, _, text = _cell_heat_style(src[col_name])
                    row.append(Paragraph(text, cell_center))
                data.append(row)

            remaining = pw - equip_w
            matrix_cols = len(cols_meta) - 1
            matrix_w = min(stage_pref_w, remaining / max(matrix_cols, 1))
            if matrix_w < stage_min_w:
                matrix_w = stage_min_w
            compact_matrix = matrix_cols >= 34
            if compact_matrix:
                matrix_w = min(matrix_w, 0.69 * cm)
            col_widths = [equip_w] + [matrix_w] * matrix_cols
            table = Table(data, colWidths=col_widths, repeatRows=3)
            table.hAlign = "LEFT"

            style_cmds = _base_left_table_style(header_rows=3, zebra_from=3) + [
                ("BACKGROUND", (0, 1), (-1, 1), palette["header_2"]),
                ("BACKGROUND", (0, 2), (-1, 2), palette["header_3"]),
                ("TEXTCOLOR", (0, 1), (-1, 2), colors.HexColor("#E2E8F0")),
                ("ALIGN", (0, 0), (-1, 2), "CENTER"),
                ("ALIGN", (0, 3), (0, -1), "LEFT"),
                ("ALIGN", (1, 3), (-1, -1), "CENTER"),
                ("SPAN", (0, 0), (0, 2)),

                # header mais compacto para evitar palavras “em pé”
                ("LEFTPADDING", (1, 0), (-1, 2), 1.0),
                ("RIGHTPADDING", (1, 0), (-1, 2), 1.0),
                ("TOPPADDING", (1, 0), (-1, 2), 2.0),
                ("BOTTOMPADDING", (1, 0), (-1, 2), 2.0),
                ("LEFTPADDING", (0, 0), (0, 2), 2.0),
                ("RIGHTPADDING", (0, 0), (0, 2), 2.0),

                # células das etapas mais quadradas e legíveis
                ("LEFTPADDING", (1, 3), (-1, -1), 2.0),
                ("RIGHTPADDING", (1, 3), (-1, -1), 2.0),
                ("TOPPADDING", (1, 3), (-1, -1), 4.8),
                ("BOTTOMPADDING", (1, 3), (-1, -1), 4.8),

                # reforço geral de grade para impressão
                ("GRID", (1, 2), (-1, -1), 0.55, colors.HexColor("#7C8A9A")),
                ("BOX", (0, 0), (-1, -1), 0.9, colors.HexColor("#475569")),
            ]
            for c1, r1, c2, r2 in spans[1:]:
                style_cmds.append(("SPAN", (c1, r1), (c2, r2)))

            if compact_matrix:
                style_cmds.extend(
                    [
                        ("FONTSIZE", (1, 0), (-1, 0), 6.1),
                        ("LEADING", (1, 0), (-1, 0), 6.5),
                        ("FONTSIZE", (1, 1), (-1, 1), 5.5),
                        ("LEADING", (1, 1), (-1, 1), 6.1),
                        ("FONTSIZE", (1, 2), (-1, 2), 5.2),
                        ("LEADING", (1, 2), (-1, 2), 5.8),
                        ("LEFTPADDING", (1, 0), (-1, 2), 0.6),
                        ("RIGHTPADDING", (1, 0), (-1, 2), 0.6),
                        ("LEFTPADDING", (1, 3), (-1, -1), 1.2),
                        ("RIGHTPADDING", (1, 3), (-1, -1), 1.2),
                        ("TOPPADDING", (1, 3), (-1, -1), 4.0),
                        ("BOTTOMPADDING", (1, 3), (-1, -1), 4.0),
                    ]
                )

            # ── Separadores de serviço (linha fina entre serviços, antes do separador de setor) ──
            n_rows_total = len(data)
            for svc_idx, (svc_start, svc_end) in enumerate(service_boundaries):
                # cor alternada no header row_servico (linha 1) e row_etapa (linha 2)
                svc_color = _svc_header_colors[svc_idx % 2]
                style_cmds.append(("BACKGROUND", (svc_start, 1), (svc_end, 1), svc_color))
                style_cmds.append(("BACKGROUND", (svc_start, 2), (svc_end, 2),
                                   colors.HexColor("#253347") if svc_idx % 2 == 0 else colors.HexColor("#344860")))

                # linha divisória direita do serviço (média — entre serviços dentro do mesmo setor)
                is_sector_boundary = svc_end in separators
                if not is_sector_boundary:
                    # linha de separação entre serviços: mais fina e numa cor intermediária
                    style_cmds.append(("LINEAFTER", (svc_end, 1), (svc_end, n_rows_total - 1),
                                       0.9, colors.HexColor("#64748B")))

            # ── Separador forte entre setores ──
            for col in separators:
                style_cmds.append(("LINEAFTER", (col, 0), (col, n_rows_total - 1),
                                   2.0, colors.HexColor("#0F172A")))

            for row_i in range(3, len(data)):
                for col_i in range(1, len(cols_meta)):
                    bg, fg, text = _cell_heat_style(view.iloc[row_i - 3, col_i])

                    # modo checklist impresso:
                    # - células vazias ficam bem destacadas para marcação manual
                    # - células OK já concluídas permanecem visíveis
                    is_done = bool(text)

                    if is_done:
                        style_cmds.extend(
                            [
                                ("BACKGROUND", (col_i, row_i), (col_i, row_i), colors.HexColor("#ECFDF5")),
                                ("TEXTCOLOR", (col_i, row_i), (col_i, row_i), colors.HexColor("#166534")),
                                ("FONTNAME", (col_i, row_i), (col_i, row_i), "Helvetica-Bold"),
                                ("BOX", (col_i, row_i), (col_i, row_i), 1.15, colors.HexColor("#22C55E")),
                            ]
                        )
                    else:
                        style_cmds.extend(
                            [
                                ("BACKGROUND", (col_i, row_i), (col_i, row_i), colors.white),
                                ("TEXTCOLOR", (col_i, row_i), (col_i, row_i), colors.white),
                                # quadrado mais forte para o gestor marcar à caneta
                                ("BOX", (col_i, row_i), (col_i, row_i), 1.2, colors.HexColor("#334155")),
                            ]
                        )

            table.setStyle(TableStyle(style_cmds))
            if len(chunks) > 1:
                blocks.append(Paragraph(f"Bloco {chunk_idx}/{len(chunks)}", small_style))
                blocks.append(Spacer(1, 0.10 * cm))
            blocks.append(table)
            if chunk_idx < len(chunks):
                blocks.append(Spacer(1, 0.34 * cm))
        return blocks

    def _build_prioridade_manual_table():
        title = Paragraph("Prioridade por Departamento x Materiais", section_style)
        subtitle = Paragraph("Preenchimento manual para priorização de materiais por departamento.", body_style)

        info = Table(
            [
                [
                    Paragraph("<b>Departamento:</b> _________________________________________________", body_style),
                    Paragraph("<b>Data:</b> ______/______/________", body_style),
                ],
                [
                    Paragraph("<b>Responsável:</b> _________________________________________________", body_style),
                    Paragraph("<b>Turno:</b> ____________________________________", body_style),
                ],
            ],
            colWidths=[pw * 0.62, pw * 0.38],
        )
        info.hAlign = "LEFT"
        info.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))

        header_style = ParagraphStyle(
            "manual_head",
            parent=small_style,
            fontSize=8.2,
            leading=9.2,
            alignment=TA_LEFT,
            textColor=colors.white,
            fontName="Helvetica-Bold",
        )

        headers = [
            Paragraph("Frota", header_style),
            Paragraph("Motivo do atraso", header_style),
            Paragraph("Setor", header_style),
        ]
        rows = [headers]
        for _ in range(12):
            rows.append([
                Paragraph("", body_style),
                Paragraph("", body_style),
                Paragraph("", body_style),
            ])

        table = Table(
            rows,
            colWidths=[pw * 0.14, pw * 0.66, pw * 0.20],
            repeatRows=1,
            rowHeights=[0.72 * cm] + [0.96 * cm] * 12,
        )
        table.hAlign = "LEFT"
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), palette["header"]),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.75, palette["line_dark"]),
            ("BOX", (0, 0), (-1, -1), 1.0, palette["line_dark"]),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, palette["panel"]]),
        ]))

        return [title, Spacer(1, 0.05 * cm), subtitle, Spacer(1, 0.15 * cm), info, Spacer(1, 0.12 * cm), table]

    resumo_cols = ["Equipamento", "Concluidos", "Total", "%"]
    if isinstance(resumo_df, pd.DataFrame) and all(c in resumo_df.columns for c in resumo_cols):
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

    def _extract_semana_mais1_from_df(df: pd.DataFrame):
        if not isinstance(df, pd.DataFrame) or df.empty:
            return None
        for col in df.columns:
            if "semana" in str(col).lower():
                serie = (
                    df[col]
                    .astype(str)
                    .str.extract(r"(\d+)", expand=False)
                )
                nums = pd.to_numeric(serie, errors="coerce").dropna()
                if not nums.empty:
                    return int(nums.max()) + 1
        return None

    def _extract_semana_from_tarefas_servico(df: pd.DataFrame, revisao_id):
        if not isinstance(df, pd.DataFrame) or df.empty or "semana" not in df.columns:
            return None

        work = df.copy()

        # filtra pela revisão atual se a coluna existir
        if revisao_id is not None and "revisao_id" in work.columns:
            try:
                work = work[work["revisao_id"].astype(str) == str(revisao_id)]
            except Exception:
                pass

        if work.empty:
            return None

        # prioriza linhas com updated_at preenchido
        if "updated_at" in work.columns:
            try:
                work["updated_at"] = pd.to_datetime(work["updated_at"], errors="coerce", utc=True)
                work = work.sort_values("updated_at")
            except Exception:
                pass

        nums = (
            work["semana"]
            .astype(str)
            .str.extract(r"(\d+)", expand=False)
            .pipe(pd.to_numeric, errors="coerce")
            .dropna()
        )
        if nums.empty:
            return None

        return int(nums.max()) + 1

    semana_impressao = None

    # prioridade 0: semana explícita de impressão (ex.: semana atual da revisão + 1)
    if semana_impressa is not None and str(semana_impressa).strip() != "":
        try:
            semana_impressao = int(pd.to_numeric([semana_impressa], errors="coerce")[0])
        except Exception:
            semana_impressao = None

    # prioridade 1: tabela tarefa_servicos da revisão atual
    if not semana_impressao:
        try:
            semana_impressao = _extract_semana_from_tarefas_servico(tarefas_servico_df, revisao_id)
        except Exception:
            semana_impressao = None

    # prioridade 2: semana explícita vinda do chamador
    if not semana_impressao:
        try:
            if semana_revisao is not None and str(semana_revisao).strip() != "":
                semana_impressao = int(pd.to_numeric([semana_revisao], errors="coerce")[0])
        except Exception:
            semana_impressao = None

    # prioridade 3: tentar inferir do resumo da revisão
    if not semana_impressao:
        try:
            semana_impressao = _extract_semana_mais1_from_df(resumo_df)
        except Exception:
            semana_impressao = None

    # fallback final: semana atual apenas se nada da revisão existir
    if not semana_impressao:
        try:
            _dt_emit = pd.to_datetime(emitido, dayfirst=True, errors="coerce")
            semana_impressao = int(getattr(_dt_emit, "isocalendar")().week) if _dt_emit is not pd.NaT else None
        except Exception:
            semana_impressao = None

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
        [[
            Paragraph("Relatório Operacional — Matriz", title_style),
            Paragraph(f'<font color="#6B7280">Data de emissão</font><br/><b>{emitido}</b>', issued_style),
        ]],
        colWidths=[pw * 0.76, pw * 0.24],
    )
    header.hAlign = "LEFT"
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    story.append(header)
    story.append(HRFlowable(width="100%", thickness=0.75, color=palette["line"], spaceAfter=5, spaceBefore=1))

    meta = Table(
        [[Paragraph("Revisão", meta_label), Paragraph("Grupo", meta_label)], [Paragraph(titulo or "—", meta_value), Paragraph(grupo_nome or "—", meta_value)]],
        colWidths=[pw * 0.38, pw * 0.62],
    )
    meta.hAlign = "LEFT"
    meta.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LINEBELOW", (0, 1), (-1, 1), 0.45, palette["line"]),
            ]
        )
    )
    story.append(meta)
    story.append(Spacer(1, 0.24 * cm))

    cards = Table(
        [[
            _kpi_card("Equipamentos", str(total_eq)),
            _kpi_card("Concluídos (100%)", f'<font color="#16A34A">{eq_100}</font>'),
            _kpi_card("Progresso médio", f"{avg_pct}%"),
            _kpi_card("Sem início (0%)", f'<font color="#EF4444">{eq_zero}</font>'),
        ]],
        colWidths=[pw / 4.0] * 4,
    )
    cards.hAlign = "LEFT"
    cards.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.append(cards)
    story.append(Spacer(1, 0.24 * cm))

    story.append(Paragraph("Resumo por equipamento", section_style))
    story.append(Spacer(1, 0.05 * cm))
    story.append(_summary_table(rv))

    merged_df, sector_groups = _merge_sector_tables(sector_tables)
    sector_pct = _compute_sector_progress(sector_tables)

    story.append(PageBreak())
    story.append(Paragraph("Matriz consolidada", section_style))
    if sector_pct:
        ordered_pct = []
        for sector in sector_groups:
            nome = sector["sector"]
            ordered_pct.append(f"{nome}: <b>{sector_pct.get(nome, 0)}%</b>")
        story.append(Paragraph(" | ".join(ordered_pct), sector_meta_style))
    story.append(Spacer(1, 0.16 * cm))

    for block in _build_consolidated_blocks(merged_df, sector_groups, sector_pct):
        story.append(block)


    story.append(PageBreak())
    for block in _build_prioridade_manual_table():
        story.append(block)

    story.append(PageBreak())
    story.append(Paragraph("Controle de checklist impresso", section_style))
    story.append(Spacer(1, 0.08 * cm))

    checklist_info = Table(
        [
            [
                Paragraph("<b>Semana impressa:</b> " + (f"Semana {semana_impressao}" if semana_impressao else "—"), body_style),
                Paragraph("<b>Data de emissão:</b> " + str(emitido or "—"), body_style),
                Paragraph("<b>Revisão:</b> " + str(titulo or "—"), body_style),
            ],
            [
                Paragraph("<b>Grupo:</b> " + str(grupo_nome or "—"), body_style),
                Paragraph("<b>Status geral:</b> (   ) OK   (   ) Pendente   (   ) Crítico", body_style),
                Paragraph("<b>Responsável:</b> ________________________________________", body_style),
            ],
        ],
        colWidths=[pw * 0.26, pw * 0.38, pw * 0.36],
    )
    checklist_info.hAlign = "LEFT"
    checklist_info.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.85, palette["line_dark"]),
        ("GRID", (0, 0), (-1, -1), 0.55, palette["line"]),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(checklist_info)
    story.append(Spacer(1, 0.16 * cm))

    assinatura = Table(
        [
            [
                Paragraph("Responsável pelo checklist", small_style),
                Paragraph("Data da conferência", small_style),
                Paragraph("Assinatura", small_style),
            ],
            [
                Paragraph("<br/><br/><br/>________________________________________", body_style),
                Paragraph("<br/><br/><br/>______/______/________", body_style),
                Paragraph("<br/><br/><br/>________________________________________", body_style),
            ],
            [
                Paragraph("Pendências / observações", small_style),
                "",
                "",
            ],
            [
                Paragraph("<br/><br/>________________________________________________________________________________________________<br/><br/>________________________________________________________________________________________________<br/><br/>________________________________________________________________________________________________", body_style),
                "",
                "",
            ],
        ],
        colWidths=[pw * 0.36, pw * 0.24, pw * 0.40],
    )
    assinatura.hAlign = "LEFT"
    assinatura.setStyle(TableStyle([
        ("SPAN", (0, 2), (2, 2)),
        ("SPAN", (0, 3), (2, 3)),
        ("BACKGROUND", (0, 0), (-1, 0), palette["panel"]),
        ("GRID", (0, 0), (-1, -1), 0.75, palette["line_dark"]),
        ("BOX", (0, 0), (-1, -1), 1.0, palette["line_dark"]),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(assinatura)

    def _footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(palette["muted"])
        canvas.drawString(LMARGIN, 0.58 * cm, "D = desmontou   R = revisou   M = montou")
        canvas.drawRightString(PAGE[0] - RMARGIN, 0.58 * cm, f"Página {canvas.getPageNumber()}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
