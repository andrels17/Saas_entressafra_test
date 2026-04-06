"""Geração do PDF consolidado de alertas."""
from __future__ import annotations

import io
from datetime import datetime
from functools import lru_cache

import pandas as pd


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



def _safe_slug(value: str) -> str:
    import re
    norm = (value or "").strip()
    norm = re.sub(r"[^\w\-. ]+", "", norm, flags=re.UNICODE)
    norm = re.sub(r"\s+", "_", norm)
    return norm.strip("._") or "arquivo"


@lru_cache(maxsize=128)
def build_group_print_pdf(
    tenant_id: str,
    revisao_id: str,
    grupo_id: str,
    grupo_nome: str,
    revisao_titulo: str,
    data_version: str = "0",
    token: str = "",
) -> bytes:
    """Gera o PDF de impressão de um grupo usando o mesmo layout da Matriz."""
    from src.ui.pages.matriz_modular.context import (
        _build_resumo_df,
        _build_sector_tables_for_export,
        _build_view_agg,
    )
    from src.ui.pages.matriz_modular.data import _load_payload
    from src.ui.pages.matriz_modular.export_tab import _extract_semana_revisao
    from src.ui.pages.matriz_modular.pdf_export import _build_pdf_tables

    payload = _load_payload(tenant_id, grupo_id, revisao_id, 5000, data_version, token) or {}
    eqs = payload.get("eqs") or []
    setor_to_services = payload.get("s2s") or {}
    all_services = payload.get("all_s") or []
    tarefas = payload.get("tarefas") or []
    if not eqs or not all_services or not setor_to_services:
        return b""

    task_map = {(str(t["equipamento_id"]), str(t["servico_id"])): t for t in tarefas if t.get("equipamento_id") and t.get("servico_id")}
    eq_label = {
        str(e["id"]): f"{e.get('frota', '')} — {e.get('modelo') or ''}".strip(" —") or str(e.get("id"))
        for e in eqs
        if e.get("id")
    }
    eq_label_short = {
        str(e["id"]): (str(e.get("frota") or "")).strip() or str(e.get("id"))
        for e in eqs
        if e.get("id")
    }

    resumo_df, _, _, _ = _build_resumo_df(eqs, all_services, task_map, eq_label)
    view_agg = _build_view_agg(eqs, all_services, task_map, eq_label)
    sector_tables = _build_sector_tables_for_export(eqs, setor_to_services, task_map, eq_label_short)
    semana_revisao = _extract_semana_revisao(
        resumo_df,
        view_agg,
        pd.concat(
            [df for _, df in sector_tables if isinstance(df, pd.DataFrame)],
            ignore_index=True,
            sort=False,
        ) if sector_tables else pd.DataFrame(),
    )
    return _build_pdf_tables(
        titulo=revisao_titulo,
        grupo_nome=grupo_nome,
        resumo_df=resumo_df,
        sector_tables=sector_tables,
        semana_revisao=semana_revisao,
        tarefas_servico_df=pd.DataFrame(tarefas),
        revisao_id=revisao_id,
    )



def build_print_zip(
    tenant_id: str,
    revisao_id: str,
    revisao_titulo: str,
    selections: list[dict],
    semana_atual: int | None = None,
    data_version: str = "0",
    token: str = "",
) -> bytes:
    """Gera um ZIP com um PDF por grupo selecionado."""
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        pdf_cache: dict[str, bytes] = {}
        used_names: set[str] = set()

        for item in selections:
            grupo_id = str(item.get("grupo_id") or "")
            grupo_nome = item.get("grupo_nome") or grupo_id
            gestor_nome = item.get("gestor_nome") or "Gestor"
            if not grupo_id:
                continue

            if grupo_id not in pdf_cache:
                pdf_cache[grupo_id] = build_group_print_pdf(
                    tenant_id=tenant_id,
                    revisao_id=revisao_id,
                    grupo_id=grupo_id,
                    grupo_nome=grupo_nome,
                    revisao_titulo=revisao_titulo,
                    data_version=data_version,
                    token=token,
                )
            pdf_bytes = pdf_cache.get(grupo_id) or b""
            if not pdf_bytes:
                continue

            semana_prefix = f"Semana_{int(semana_atual):02d}_" if semana_atual else ""
            base_name = f"{semana_prefix}{_safe_slug(gestor_nome)}__{_safe_slug(grupo_nome)}.pdf"
            file_name = base_name
            suffix = 2
            while file_name in used_names:
                file_name = base_name.replace(".pdf", f"_{suffix}.pdf")
                suffix += 1
            used_names.add(file_name)
            zf.writestr(file_name, pdf_bytes)

    return buf.getvalue()
