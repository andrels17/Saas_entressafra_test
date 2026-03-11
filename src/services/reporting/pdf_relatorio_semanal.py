"""PDF de Relatório Semanal por Departamento.

Conteúdo (todas as seções):
  Página 1 — Capa: KPIs do departamento + barra de progresso geral
  Página 2 — Evolução semanal: tabela de progresso por semana (S1…Sn)
              + Comparativo semana anterior vs atual
  Página 3 — Equipamentos críticos: 0% ou travados
  Página 4 — Resumo de alertas: travados / sem início / parados / risco prazo

Segue o mesmo padrão visual de pdf_executivo.py (canvas direto do ReportLab).
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle


# ── Paleta ────────────────────────────────────────────────────────────────────
FG       = colors.Color(0.10, 0.12, 0.16)
MUTED    = colors.Color(0.42, 0.46, 0.52)
SURFACE  = colors.Color(0.96, 0.97, 0.98)
BORDER   = colors.Color(0.88, 0.90, 0.93)
GREEN    = colors.HexColor("#12B76A")
YELLOW   = colors.HexColor("#F59E0B")
RED      = colors.HexColor("#EF4444")
DARK     = colors.HexColor("#111827")
WHITE    = colors.white

def _risk_color(pct: int) -> colors.Color:
    if pct >= 80: return GREEN
    if pct >= 50: return YELLOW
    return RED

def _hex(h: str, fallback=colors.gray) -> colors.Color:
    try:
        s = h.lstrip("#")
        r, g, b = int(s[0:2],16)/255, int(s[2:4],16)/255, int(s[4:6],16)/255
        return colors.Color(r, g, b)
    except Exception:
        return fallback


# ── Payload de dados ──────────────────────────────────────────────────────────
@dataclass
class SemanaSnapshot:
    semana: int
    concluidos: int      # etapas concluídas (D+R+M) nessa semana
    total: int           # total possível
    pct: int


@dataclass
class EquipamentoCritico:
    frota: str
    modelo: str
    grupo: str
    pct: int
    status: str          # "zero" | "travado" | "atrasado"
    n_alertas: int = 0


@dataclass
class RelatorioDeptPayload:
    tenant_nome: str
    departamento_nome: str
    revisao_titulo: str
    semana_atual: int
    semanas_total: int
    data_inicio: str | None         # ISO
    # progresso
    pct_geral: int
    n_equipamentos: int
    n_concluidos: int               # equipamentos 100%
    n_alertas_total: int
    # evolução semanal
    evolucao: List[SemanaSnapshot]
    # comparativo
    pct_semana_anterior: int
    pct_semana_atual: int
    # críticos
    criticos: List[EquipamentoCritico]
    # todos os equipamentos (para tabela de progresso geral no PDF)
    todos_equipamentos: List[dict] = None  # [{frota, modelo, grupo, pct, status}]
    # alertas consolidados
    n_travados: int = 0
    n_sem_inicio: int = 0
    n_parados: int = 0
    n_risco_prazo: int = 0
    # branding
    primary_color: str = "#FFD100"
    logo_url: str | None = None


# ── Gerador ───────────────────────────────────────────────────────────────────
def build_weekly_pdf(payload: RelatorioDeptPayload) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    PRIMARY = _hex(payload.primary_color)
    page_no = [0]   # mutável dentro das closures

    # ── helpers comuns ────────────────────────────────────────────────────────
    def new_page(title: str):
        page_no[0] += 1
        # fundo branco
        c.setFillColor(WHITE); c.rect(0, 0, w, h, fill=1, stroke=0)
        # faixa superior escura
        c.setFillColor(DARK); c.rect(0, h-26*mm, w, 26*mm, fill=1, stroke=0)
        # linha de acento colorida
        c.setFillColor(PRIMARY); c.rect(0, h-26*mm, w, 2.5, fill=1, stroke=0)
        # nome do tenant + departamento
        c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 14)
        c.drawString(16*mm, h-14*mm, payload.tenant_nome)
        c.setFont("Helvetica", 9)
        c.setFillColor(colors.Color(1,1,1,.6))
        period = f"{payload.revisao_titulo}  ·  Semana {payload.semana_atual}/{payload.semanas_total}"
        c.drawString(16*mm, h-20*mm, period)
        # título da página
        c.setFillColor(FG); c.setFont("Helvetica-Bold", 15)
        c.drawString(16*mm, h-38*mm, title)
        # subtítulo departamento
        c.setFillColor(MUTED); c.setFont("Helvetica", 10)
        c.drawString(16*mm, h-45*mm, f"Departamento: {payload.departamento_nome}")
        # divider
        c.setStrokeColor(BORDER); c.setLineWidth(0.8)
        c.line(16*mm, h-48*mm, w-16*mm, h-48*mm)

    def footer():
        c.setStrokeColor(BORDER); c.setLineWidth(0.8)
        c.line(16*mm, 14*mm, w-16*mm, 14*mm)
        c.setFillColor(MUTED); c.setFont("Helvetica", 8)
        c.drawString(16*mm, 9*mm, "Relatório gerado automaticamente — sistema de gestão de revisões.")
        now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
        c.drawRightString(w-16*mm, 9*mm, f"{now_str}  ·  pág. {page_no[0]}")

    def section_title(txt: str, y: float) -> float:
        c.setFillColor(FG); c.setFont("Helvetica-Bold", 11)
        c.drawString(16*mm, y, txt)
        c.setStrokeColor(BORDER); c.setLineWidth(0.5)
        c.line(16*mm, y-2*mm, w-16*mm, y-2*mm)
        return y - 8*mm

    def kpi_row(items: List[Tuple[str, str, colors.Color]], top_y: float, card_h: float = 20*mm) -> float:
        """Renderiza uma linha de KPI cards. items = [(label, valor, cor_valor)]"""
        n = len(items)
        gap = 4*mm
        total_gap = gap * (n-1)
        card_w = (w - 32*mm - total_gap) / n
        x0 = 16*mm
        for i, (label, val, val_color) in enumerate(items):
            cx = x0 + i*(card_w + gap)
            cy = top_y - card_h
            c.setFillColor(SURFACE); c.setStrokeColor(BORDER); c.setLineWidth(0.7)
            c.roundRect(cx, cy, card_w, card_h, 5, fill=1, stroke=1)
            c.setFillColor(PRIMARY); c.rect(cx, top_y-2.5, card_w, 2.5, fill=1, stroke=0)
            c.setFillColor(MUTED); c.setFont("Helvetica", 8)
            c.drawString(cx+6, top_y-8*mm, label)
            c.setFillColor(val_color); c.setFont("Helvetica-Bold", 18)
            c.drawString(cx+6, cy+5*mm, val)
        return top_y - card_h - 8*mm

    def progress_bar(x: float, y: float, bar_w: float, bar_h: float, pct: int):
        pct_c = max(0, min(100, pct))
        c.setFillColor(BORDER); c.roundRect(x, y, bar_w, bar_h, 2, fill=1, stroke=0)
        if pct_c > 0:
            c.setFillColor(_risk_color(pct_c))
            c.roundRect(x, y, bar_w * pct_c/100, bar_h, 2, fill=1, stroke=0)

    def platypus_table(rows: List[List], col_widths: List[float], top_y: float,
                       header_color=DARK, alt_surface=True) -> float:
        """Desenha uma Table do platypus no canvas. Retorna y após a tabela."""
        tbl = Table(rows, colWidths=col_widths)
        style = [
            ("BACKGROUND",      (0,0), (-1,0), header_color),
            ("TEXTCOLOR",       (0,0), (-1,0), WHITE),
            ("FONTNAME",        (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",        (0,0), (-1,0), 8.5),
            ("ALIGN",           (0,0), (-1,0), "CENTER"),
            ("VALIGN",          (0,0), (-1,-1), "MIDDLE"),
            ("FONTSIZE",        (0,1), (-1,-1), 8),
            ("GRID",            (0,0), (-1,-1), 0.3, BORDER),
            ("LEFTPADDING",     (0,0), (-1,-1), 5),
            ("RIGHTPADDING",    (0,0), (-1,-1), 5),
            ("TOPPADDING",      (0,0), (-1,-1), 4),
            ("BOTTOMPADDING",   (0,0), (-1,-1), 4),
            ("ALIGN",           (1,1), (-1,-1), "CENTER"),
        ]
        if alt_surface:
            for r in range(1, len(rows)):
                if r % 2 == 0:
                    style.append(("BACKGROUND", (0,r), (-1,r), SURFACE))
        tbl.setStyle(TableStyle(style))
        tw, th = tbl.wrapOn(c, w - 32*mm, h)
        draw_y = top_y - th
        tbl.drawOn(c, 16*mm, draw_y)
        return draw_y - 6*mm

    # ── PÁGINA 1: Capa / KPIs ─────────────────────────────────────────────────
    new_page("Relatório Semanal")
    y = h - 52*mm

    # KPIs linha 1
    pct_color = _risk_color(payload.pct_geral)
    y = kpi_row([
        ("Progresso geral",        f"{payload.pct_geral}%",    pct_color),
        ("Equipamentos",           str(payload.n_equipamentos), FG),
        ("Concluídos (100%)",      str(payload.n_concluidos),   GREEN),
        ("Alertas ativos",         str(payload.n_alertas_total),
         RED if payload.n_alertas_total else GREEN),
    ], top_y=y)

    # Barra de progresso geral grande
    y -= 4*mm
    c.setFillColor(FG); c.setFont("Helvetica-Bold", 10)
    c.drawString(16*mm, y, "Progresso geral do departamento")
    y -= 10*mm
    bar_w = w - 32*mm
    progress_bar(16*mm, y, bar_w, 8*mm, payload.pct_geral)
    c.setFillColor(_risk_color(payload.pct_geral)); c.setFont("Helvetica-Bold", 9)
    c.drawRightString(w-16*mm, y+9*mm, f"{payload.pct_geral}%")
    y -= 16*mm

    # KPIs linha 2 — alertas por tipo
    y = kpi_row([
        ("🚫 Travados",    str(payload.n_travados),   RED   if payload.n_travados   else MUTED),
        ("⬜ Sem início",  str(payload.n_sem_inicio), MUTED if not payload.n_sem_inicio else YELLOW),
        ("⏸ Parados",     str(payload.n_parados),    MUTED if not payload.n_parados    else YELLOW),
        ("⚠️ Risco prazo", str(payload.n_risco_prazo),MUTED if not payload.n_risco_prazo else RED),
    ], top_y=y, card_h=18*mm)

    # Comparativo rápido S-1 vs S atual
    y -= 2*mm
    y = section_title("Comparativo — semana anterior vs. atual", y)
    delta = payload.pct_semana_atual - payload.pct_semana_anterior
    delta_str = f"+{delta}p.p." if delta >= 0 else f"{delta}p.p."
    delta_color = GREEN if delta >= 0 else RED

    box_w = (w - 32*mm - 8*mm) / 3
    boxes = [
        ("Semana anterior", f"{payload.pct_semana_anterior}%", _risk_color(payload.pct_semana_anterior)),
        ("Semana atual",    f"{payload.pct_semana_atual}%",    _risk_color(payload.pct_semana_atual)),
        ("Variação",        delta_str,                          delta_color),
    ]
    bx = 16*mm
    for label, val, col in boxes:
        c.setFillColor(SURFACE); c.setStrokeColor(BORDER); c.setLineWidth(0.7)
        c.roundRect(bx, y-16*mm, box_w, 16*mm, 5, fill=1, stroke=1)
        c.setFillColor(MUTED); c.setFont("Helvetica", 8)
        c.drawString(bx+6, y-6*mm, label)
        c.setFillColor(col); c.setFont("Helvetica-Bold", 16)
        c.drawString(bx+6, y-14*mm, val)
        bx += box_w + 4*mm
    y -= 24*mm

    footer(); c.showPage()

    # ── PÁGINA 2: Evolução semanal ────────────────────────────────────────────
    new_page("Evolução Semanal")
    y = h - 52*mm

    y = section_title("Progresso acumulado por semana", y)

    if payload.evolucao:
        # Mini barras horizontais por semana
        bar_area_w = w - 32*mm
        label_w    = 18*mm
        pct_w      = 12*mm
        bar_avail  = bar_area_w - label_w - pct_w - 6*mm
        row_h      = 7*mm
        sem_atual  = payload.semana_atual

        for snap in payload.evolucao:
            if y < 30*mm:
                footer(); c.showPage(); new_page("Evolução Semanal (cont.)"); y = h-52*mm

            is_current = snap.semana == sem_atual
            label = f"Semana {snap.semana}"
            if is_current:
                c.setFillColor(PRIMARY); c.setFont("Helvetica-Bold", 8.5)
            else:
                c.setFillColor(MUTED); c.setFont("Helvetica", 8.5)
            c.drawString(16*mm, y - row_h/2, label)

            bx = 16*mm + label_w
            progress_bar(bx, y - row_h + 1*mm, bar_avail, row_h - 3*mm, snap.pct)

            pct_col = _risk_color(snap.pct)
            c.setFillColor(pct_col); c.setFont("Helvetica-Bold", 8.5)
            c.drawRightString(w - 16*mm, y - row_h/2, f"{snap.pct}%")

            y -= row_h + 1.5*mm

        y -= 6*mm

        # Tabela resumo evolução
        y = section_title("Tabela de evolução", y)
        rows = [["Semana", "Etapas feitas", "Total possível", "%"]]
        for snap in payload.evolucao:
            rows.append([
                f"Sem. {snap.semana}" + (" ◀ atual" if snap.semana == sem_atual else ""),
                str(snap.concluidos),
                str(snap.total),
                f"{snap.pct}%",
            ])
        col_w = bar_area_w / 4
        y = platypus_table(rows, [col_w*1.4, col_w*1.0, col_w*1.0, col_w*0.6], y)
    else:
        c.setFillColor(MUTED); c.setFont("Helvetica", 10)
        c.drawString(16*mm, y-10*mm, "Sem dados de evolução semanal para esta revisão.")

    footer(); c.showPage()

    # ── PÁGINA 3: Equipamentos críticos ───────────────────────────────────────
    new_page("Equipamentos Críticos")
    y = h - 52*mm

    status_labels = {"zero": "0% — Sem início", "travado": "Travado", "atrasado": "Atrasado"}
    status_colors = {"zero": RED, "travado": RED, "atrasado": YELLOW}

    if payload.criticos:
        y = section_title(f"{len(payload.criticos)} equipamento(s) requerem atenção", y)
        rows = [["Frota", "Modelo", "Grupo", "Progresso", "Situação"]]
        for eq in payload.criticos:
            rows.append([
                eq.frota,
                eq.modelo[:20] if eq.modelo else "—",
                eq.grupo[:18] if eq.grupo else "—",
                f"{eq.pct}%",
                status_labels.get(eq.status, eq.status),
            ])
        pw_useful = w - 32*mm
        y = platypus_table(rows, [pw_useful*0.12, pw_useful*0.26, pw_useful*0.24,
                                   pw_useful*0.14, pw_useful*0.24], y)

        # Mini barras individuais por equipamento crítico
        y -= 2*mm
        y = section_title("Progresso individual dos críticos", y)
        for eq in payload.criticos:
            if y < 30*mm:
                footer(); c.showPage(); new_page("Equipamentos Críticos (cont.)"); y = h-52*mm
            col = status_colors.get(eq.status, RED)
            c.setFillColor(FG); c.setFont("Helvetica-Bold", 8.5)
            label = f"{eq.frota}  —  {eq.modelo or ''}"
            c.drawString(16*mm, y, label[:42])
            c.setFillColor(MUTED); c.setFont("Helvetica", 7.5)
            c.drawString(16*mm, y-4*mm, eq.grupo[:30] if eq.grupo else "")
            bx = 70*mm
            bar_avail = w - 32*mm - 54*mm
            progress_bar(bx, y - 4*mm, bar_avail, 5*mm, eq.pct)
            c.setFillColor(col); c.setFont("Helvetica-Bold", 8.5)
            c.drawRightString(w-16*mm, y, f"{eq.pct}%")
            y -= 11*mm
    else:
        c.setFillColor(GREEN); c.setFont("Helvetica-Bold", 14)
        c.drawString(16*mm, y-10*mm, "✓ Nenhum equipamento crítico neste departamento.")

    footer(); c.showPage()

    # ── PÁGINA 3b: Progresso de todos os equipamentos ─────────────────────────
    todos = payload.todos_equipamentos or []
    if todos:
        new_page("Progresso de Todos os Equipamentos")
        y = h - 52*mm

        y = section_title(f"Progresso individual — {len(todos)} equipamento(s)", y)

        label_w  = 40*mm
        pct_w    = 14*mm
        bar_avail = w - 32*mm - label_w - pct_w - 6*mm
        row_h    = 8*mm

        status_labels = {
            "concluido":    "✓",
            "travado":      "⚠",
            "zero":         "—",
            "em_andamento": "",
            "sem_template": "?",
        }

        for eq in todos:
            if y < 25*mm:
                footer(); c.showPage()
                new_page("Progresso de Todos os Equipamentos (cont.)")
                y = h - 52*mm
                y = section_title("continuação", y)

            pct    = int(eq.get("pct", 0))
            frota  = str(eq.get("frota") or "—")
            modelo = str(eq.get("modelo") or "")[:18]
            grupo  = str(eq.get("grupo") or "")[:16]
            status = eq.get("status") or ""
            col    = _risk_color(pct)

            # linha de fundo alternada
            c.setFillColor(SURFACE if todos.index(eq) % 2 == 0 else WHITE)
            c.rect(16*mm, y - row_h, w - 32*mm, row_h, fill=1, stroke=0)

            # frota (bold) + modelo
            c.setFillColor(FG); c.setFont("Helvetica-Bold", 8)
            c.drawString(17*mm, y - row_h*0.38, frota)
            c.setFillColor(MUTED); c.setFont("Helvetica", 7)
            c.drawString(17*mm, y - row_h*0.72, modelo)

            # grupo
            c.setFillColor(MUTED); c.setFont("Helvetica", 7.5)
            c.drawString(17*mm + label_w, y - row_h/2, grupo)

            # barra de progresso
            bx = 17*mm + label_w + 28*mm
            progress_bar(bx, y - row_h + 1.5*mm, bar_avail, row_h - 3*mm, pct)

            # %
            badge = status_labels.get(status, "")
            c.setFillColor(col); c.setFont("Helvetica-Bold", 8)
            c.drawRightString(w - 17*mm, y - row_h*0.4, f"{pct}%")
            if badge:
                c.setFillColor(RED if status in ("travado","zero") else MUTED)
                c.setFont("Helvetica-Bold", 7)
                c.drawRightString(w - 17*mm, y - row_h*0.72, badge)

            # divisor leve
            c.setStrokeColor(BORDER); c.setLineWidth(0.3)
            c.line(16*mm, y - row_h, w - 16*mm, y - row_h)

            y -= row_h

    footer(); c.showPage()

    # ── PÁGINA 4: Resumo de alertas ───────────────────────────────────────────
    new_page("Resumo de Alertas")
    y = h - 52*mm

    alert_items = [
        ("🚫 Travados sem resolução",      payload.n_travados,   RED,   "Tarefas com status travado sem atualização recente."),
        ("⬜ Sem nenhum apontamento",      payload.n_sem_inicio, MUTED, "Tarefas onde D/R/M nunca foram marcados."),
        ("⏸ Parados (sem atualização)",   payload.n_parados,    YELLOW,"Tarefas em andamento sem atualização no período."),
        ("⚠️ Em risco de não concluir",   payload.n_risco_prazo,RED,   "Progresso abaixo da meta linear da semana."),
    ]

    for title_a, count, col, desc in alert_items:
        if y < 40*mm:
            footer(); c.showPage(); new_page("Alertas (cont.)"); y = h-52*mm

        # card de alerta
        card_h = 22*mm
        c.setFillColor(SURFACE); c.setStrokeColor(BORDER); c.setLineWidth(0.7)
        c.roundRect(16*mm, y-card_h, w-32*mm, card_h, 5, fill=1, stroke=1)
        # borda colorida lateral esquerda
        c.setFillColor(col)
        c.rect(16*mm, y-card_h, 3, card_h, fill=1, stroke=0)

        # número
        c.setFillColor(col); c.setFont("Helvetica-Bold", 22)
        c.drawString(24*mm, y-15*mm, str(count))

        # título e descrição
        c.setFillColor(FG); c.setFont("Helvetica-Bold", 10)
        c.drawString(40*mm, y-7*mm, title_a)
        c.setFillColor(MUTED); c.setFont("Helvetica", 8.5)
        c.drawString(40*mm, y-13*mm, desc)

        # badge ok/alerta
        badge_txt = "OK" if count == 0 else f"{count} item{'s' if count > 1 else ''}"
        badge_col = GREEN if count == 0 else col
        c.setFillColor(badge_col); c.setFont("Helvetica-Bold", 8.5)
        c.drawRightString(w-20*mm, y-10*mm, badge_txt)

        y -= card_h + 4*mm

    # Rodapé da página de alertas
    y -= 4*mm
    c.setFillColor(MUTED); c.setFont("Helvetica", 8.5)
    note = (f"Relatório gerado em {datetime.now().strftime('%d/%m/%Y às %H:%M')} "
            f"para a revisão '{payload.revisao_titulo}', "
            f"semana {payload.semana_atual} de {payload.semanas_total}.")
    c.drawString(16*mm, max(y, 30*mm), note)

    footer(); c.showPage()
    c.save()
    return buf.getvalue()
