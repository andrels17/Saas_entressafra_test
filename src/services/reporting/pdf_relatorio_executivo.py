"""PDF Executivo — visão consolidada para supervisores e diretores.

Melhorias incluídas:
  • Top 5 evolução da semana e Top 5 mais atrasados
  • Tendência semanal (últimas 4 semanas, opcional via payload.trend_semanal)
  • Alertas inteligentes de equipamentos sem movimentação
  • Heatmap departamentos × semanas (opcional via payload.heatmap_semanal)
  • Layout mais limpo com cores e escalas padronizadas
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import List

from src.utils.timezone import fmt_brt
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

FG = colors.Color(0.10, 0.12, 0.16)
MUTED = colors.Color(0.42, 0.46, 0.52)
SURFACE = colors.Color(0.96, 0.97, 0.98)
BORDER = colors.Color(0.88, 0.90, 0.93)
GREEN = colors.HexColor("#12B76A")
YELLOW = colors.HexColor("#F59E0B")
ORANGE = colors.HexColor("#F97316")
RED = colors.HexColor("#EF4444")
BLUE = colors.HexColor("#3B82F6")
INDIGO = colors.HexColor("#6366F1")
DARK = colors.HexColor("#111827")
WHITE = colors.white


@dataclass
class DeptSnapshot:
    nome: str
    pct_geral: int
    pct_anterior: int
    n_equipamentos: int
    n_concluidos: int
    n_travados: int
    n_sem_inicio: int
    n_risco_prazo: int
    top_criticos: list[dict]
    top_melhores: list[dict]
    maiores_evolucoes: list[dict]
    n_parados: int = 0
    max_dias_parado: int = 0
    _done_steps: int = 0
    _expected_steps: int = 0


@dataclass
class RelatorioExecutivoPayload:
    tenant_nome: str
    revisao_titulo: str
    semana_atual: int
    semanas_total: int
    pct_global: int
    n_equip_total: int
    n_equip_concluidos: int
    n_alertas_total: int
    departamentos: List[DeptSnapshot]
    primary_color: str = "#FFD100"
    logo_url: str | None = None
    trend_semanal: list[dict] | None = None          # [{semana, pct}]
    # [{departamento, semana, pct}]
    heatmap_semanal: list[dict] | None = None
    # {atencao, critico, urgente}
    alertas_parados: dict | None = None


def _risk_color(pct: int) -> colors.Color:
    if pct >= 80:
        return GREEN
    if pct >= 50:
        return YELLOW
    return RED


def _stoppage_color(days: int) -> colors.Color:
    if days > 21:
        return RED
    if days > 14:
        return ORANGE
    if days > 7:
        return YELLOW
    return GREEN


def _hex(h: str) -> colors.Color:
    try:
        s = h.lstrip('#')
        r, g, b = int(s[0:2], 16) / 255, int(s[2:4], 16) / \
            255, int(s[4:6], 16) / 255
        return colors.Color(r, g, b)
    except Exception:
        return colors.gray


def build_executive_pdf(payload: RelatorioExecutivoPayload) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    accent = _hex(payload.primary_color)
    now = fmt_brt()

    def footer():
        c.setFillColor(MUTED)
        c.setFont('Helvetica', 7.5)
        c.drawString(16 * mm, 10 * mm,
                     f"{payload.tenant_nome} · Relatório Executivo · {now}")
        c.drawRightString(
            w - 16 * mm, 10 * mm, f"Semana {payload.semana_atual}/{payload.semanas_total}")

    def page_header(title: str):
        c.setFillColor(DARK)
        c.rect(0, h - 13 * mm, w, 13 * mm, fill=1, stroke=0)
        c.setFillColor(accent)
        c.rect(0, h - 13 * mm, 4, 13 * mm, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont('Helvetica-Bold', 9)
        c.drawString(10 * mm, h - 8.5 * mm, title)
        c.setFillColor(colors.Color(0.6, 0.65, 0.7))
        c.setFont('Helvetica', 8)
        c.drawRightString(w - 10 * mm, h - 8.5 * mm, payload.revisao_titulo)

    def pbar(x, y, bw, bh, pct, color=None):
        col = color or _risk_color(int(pct or 0))
        c.setFillColor(BORDER)
        c.roundRect(x, y, bw, bh, bh / 2, fill=1, stroke=0)
        fw = max(bw * max(0, min(int(pct or 0), 100)) /
                 100, bh if pct and pct > 0 else 0)
        if fw > 0:
            c.setFillColor(col)
            c.roundRect(x, y, fw, bh, bh / 2, fill=1, stroke=0)

    def delta_str(d: int) -> str:
        return f"+{d}p.p." if d > 0 else f"{d}p.p."

    def real_pct_dept(dept: DeptSnapshot) -> int:
        expected = int(getattr(dept, "_expected_steps", 0) or 0)
        done = int(getattr(dept, "_done_steps", 0) or 0)
        if expected > 0:
            return max(0, min(100, int(round(done / expected * 100))))
        return max(0, min(100, int(getattr(dept, "pct_geral", 0) or 0)))

    def real_delta_dept(dept: DeptSnapshot) -> int:
        return int(real_pct_dept(dept)) - int(getattr(dept, "pct_anterior", 0) or 0)

    def section_title(txt: str, y: float) -> float:
        c.setFillColor(accent)
        c.rect(16 * mm, y - 0.8 * mm, 3, 5 * mm, fill=1, stroke=0)
        c.setFillColor(FG)
        c.setFont('Helvetica-Bold', 10)
        c.drawString(21 * mm, y, txt)
        return y - 9 * mm

    def mini_kpi_row(y_top: float,
                     items: list[tuple[str,
                                       str,
                                       colors.Color]],
                     cols: int = 4) -> float:
        card_h = 16 * mm
        gap = 2 * mm
        card_w = (w - 32 * mm - (cols - 1) * gap) / cols
        for i, (lbl, val, col) in enumerate(items):
            cx = 16 * mm + i * (card_w + gap)
            c.setFillColor(SURFACE)
            c.setStrokeColor(BORDER)
            c.setLineWidth(0.6)
            c.roundRect(
                cx,
                y_top -
                card_h,
                card_w,
                card_h,
                3,
                fill=1,
                stroke=1)
            c.setFillColor(col)
            c.setFont('Helvetica-Bold', 16)
            c.drawString(cx + 4, y_top - 10 * mm, val)
            c.setFillColor(MUTED)
            c.setFont('Helvetica', 7.5)
            c.drawString(cx + 4, y_top - 14 * mm, lbl)
        return y_top - card_h - 6 * mm

    deptos = sorted(payload.departamentos,
                    key=lambda d: (-real_pct_dept(d), d.nome))
    n_deptos = len(deptos)
    n_verde = sum(1 for d in deptos if real_pct_dept(d) >= 80)
    n_amarelo = sum(1 for d in deptos if 50 <= real_pct_dept(d) < 80)
    n_vermelho = sum(1 for d in deptos if real_pct_dept(d) < 50)
    total_parados = sum(int(getattr(d, 'n_parados', 0) or 0) for d in deptos)
    deptos_com_parados = [
        d for d in deptos if int(
            getattr(
                d,
                'n_parados',
                0) or 0) > 0]
    maior_dias = max([int(getattr(d, 'max_dias_parado', 0) or 0)
                     for d in deptos] or [0])
    top_atrasados = sorted(deptos, key=lambda d: (
        real_pct_dept(d), -int(d.n_travados or 0), d.nome))[:5]
    top_evolucao = sorted(deptos, key=lambda d: (
        real_delta_dept(d), real_pct_dept(d)
    ), reverse=True)[:5]

    alertas_cfg = payload.alertas_parados or {}
    if alertas_cfg:
        n_atencao = int(alertas_cfg.get('atencao', 0) or 0)
        n_critico = int(alertas_cfg.get('critico', 0) or 0)
        n_urgente = int(alertas_cfg.get('urgente', 0) or 0)
    else:
        n_urgente = sum(int(getattr(d, 'n_parados', 0) or 0) for d in deptos if int(
            getattr(d, 'max_dias_parado', 0) or 0) > 21)
        n_critico = sum(int(getattr(d, 'n_parados', 0) or 0) for d in deptos if 14 < int(
            getattr(d, 'max_dias_parado', 0) or 0) <= 21)
        n_atencao = sum(int(getattr(d, 'n_parados', 0) or 0) for d in deptos if 7 < int(
            getattr(d, 'max_dias_parado', 0) or 0) <= 14)

    total_done_exec = sum(int(getattr(d, "_done_steps", 0) or 0) for d in deptos)
    total_expected_exec = sum(int(getattr(d, "_expected_steps", 0) or 0) for d in deptos)
    pct_global_real = (
        max(0, min(100, int(round(total_done_exec / total_expected_exec * 100))))
        if total_expected_exec > 0 else int(payload.pct_global or 0)
    )

    # Página 1
    c.setFillColor(DARK)
    c.rect(0, h - 38 * mm, w, 38 * mm, fill=1, stroke=0)
    c.setFillColor(accent)
    c.rect(0, h - 38 * mm, w, 2, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont('Helvetica-Bold', 18)
    c.drawString(16 * mm, h - 14 * mm, 'Relatório Executivo de Revisão')
    c.setFillColor(colors.Color(0.65, 0.70, 0.78))
    c.setFont('Helvetica', 10)
    c.drawString(
        16 * mm, h - 22 * mm, f"{payload.revisao_titulo}  ·  Semana {payload.semana_atual} de {payload.semanas_total}  ·  {payload.tenant_nome}  ·  {now}")

    c.setFillColor(colors.Color(0.18, 0.21, 0.27))
    c.roundRect(
        16 * mm,
        h - 34 * mm,
        w - 32 * mm,
        7 * mm,
        3.5,
        fill=1,
        stroke=0)
    fw = max((w - 32 * mm) * pct_global_real / 100,
             7 * mm if pct_global_real > 0 else 0)
    c.setFillColor(_risk_color(pct_global_real))
    c.roundRect(16 * mm, h - 34 * mm, fw, 7 * mm, 3.5, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont('Helvetica-Bold', 9)
    c.drawRightString(w - 18 * mm, h - 31 * mm,
                      f"{pct_global_real}% concluído")

    y = h - 40 * mm
    y = mini_kpi_row(y, [
        ('Departamentos', str(n_deptos), DARK),
        ('Equipamentos', str(payload.n_equip_total), DARK),
        ('Concluídos', str(payload.n_equip_concluidos), GREEN),
        ('Alertas', str(payload.n_alertas_total), RED if payload.n_alertas_total else GREEN),
    ])

    badge_data = [
        (f"{n_verde} em dia  (≥80%)", GREEN),
        (f"{n_amarelo} atenção  (50–79%)", YELLOW),
        (f"{n_vermelho} críticos  (<50%)", RED),
    ]
    bw3 = (w - 36 * mm) / 3
    for i, (txt, col) in enumerate(badge_data):
        bx = 16 * mm + i * (bw3 + 2 * mm)
        c.setFillColor(col)
        c.roundRect(bx, y - 7 * mm, bw3, 7 * mm, 3.5, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont('Helvetica-Bold', 8)
        c.drawString(bx + 5, y - 4.5 * mm, txt)
    y -= 13 * mm

    c.setFillColor(DARK)
    c.rect(16 * mm, y - 7 * mm, w - 32 * mm, 7 * mm, fill=1, stroke=0)
    c.setFillColor(colors.Color(0.6, 0.65, 0.7))
    c.setFont('Helvetica-Bold', 7.5)
    c.drawString(22 * mm, y - 5 * mm, '#  Departamento')
    c.drawString(w / 2 + 2 * mm, y - 5 * mm, 'Progresso')
    c.drawRightString(w - 18 * mm, y - 5 * mm, '%')
    y -= 7 * mm
    row_h = 11 * mm
    for i, dept in enumerate(deptos):
        if y - row_h < 18 * mm:
            footer()
            c.showPage()
            page_header('Ranking de Departamentos (cont.)')
            y = h - 20 * mm
        c.setFillColor(SURFACE if i % 2 == 0 else WHITE)
        c.rect(16 * mm, y - row_h, w - 32 * mm, row_h, fill=1, stroke=0)
        dept_pct = real_pct_dept(dept)
        col = _risk_color(dept_pct)
        c.setFillColor(col)
        c.rect(16 * mm, y - row_h, 3, row_h, fill=1, stroke=0)
        c.setFillColor(MUTED)
        c.setFont('Helvetica', 8)
        c.drawString(22 * mm, y - row_h / 2, f"#{i + 1}")
        c.setFillColor(FG)
        c.setFont('Helvetica-Bold', 8.5)
        c.drawString(32 * mm, y - row_h * 0.38, dept.nome[:30])
        c.setFillColor(MUTED)
        c.setFont('Helvetica', 7)
        c.drawString(
            32 * mm, y - row_h * 0.72, f"{dept.n_equipamentos} equip · {dept.n_concluidos} OK · {dept.n_travados} travados · {dept.n_sem_inicio} sem início")
        bar_x = w / 2
        bar_w = w - 32 * mm - (bar_x - 16 * mm) - 18 * mm
        pbar(bar_x, y - row_h + 3 * mm, bar_w, 5 * mm, dept_pct)
        c.setFillColor(col)
        c.setFont('Helvetica-Bold', 9)
        c.drawRightString(w - 18 * mm, y - row_h * 0.38, f"{dept_pct}%")
        delta = dept_pct - int(dept.pct_anterior or 0)
        if delta != 0:
            c.setFillColor(GREEN if delta > 0 else RED)
            c.setFont('Helvetica-Bold', 7)
            c.drawRightString(w - 18 * mm, y - row_h * 0.75, delta_str(delta))
        c.setStrokeColor(BORDER)
        c.setLineWidth(0.3)
        c.line(16 * mm, y - row_h, w - 16 * mm, y - row_h)
        y -= row_h

    footer()
    c.showPage()

    # Página 2 - ranking top 5 e tendência
    page_header('Top 5 departamentos e tendência semanal')
    y = h - 20 * mm
    c.setFillColor(FG)
    c.setFont('Helvetica-Bold', 10)
    c.drawString(16 * mm, y, 'Destaques da semana')
    y -= 6 * mm

    col_gap = 4 * mm
    col_w = (w - 32 * mm - col_gap) / 2
    left_x = 16 * mm
    right_x = left_x + col_w + col_gap

    def draw_top5_box(x, y_top, width, title, items, mode='delta'):
        box_h = 56 * mm
        c.setFillColor(SURFACE)
        c.setStrokeColor(BORDER)
        c.roundRect(x, y_top - box_h, width, box_h, 3, fill=1, stroke=1)
        c.setFillColor(DARK)
        c.rect(x, y_top - 8 * mm, width, 8 * mm, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont('Helvetica-Bold', 8)
        c.drawString(x + 4, y_top - 5.4 * mm, title)
        ry = y_top - 13 * mm
        for idx, d in enumerate(items[:5], start=1):
            pct = real_pct_dept(d)
            delta = pct - int(d.pct_anterior or 0)
            c.setFillColor(MUTED)
            c.setFont('Helvetica', 7)
            c.drawString(x + 4, ry, f"#{idx}")
            c.setFillColor(FG)
            c.setFont('Helvetica-Bold', 7.8)
            c.drawString(x + 11, ry, d.nome[:19])
            pbar(x + width - 31 * mm, ry - 1.7 * mm, 17 * mm, 3.6 * mm, pct)
            if mode == 'delta':
                c.setFillColor(GREEN if delta >= 0 else RED)
                c.setFont('Helvetica-Bold', 6.8)
                c.drawRightString(x + width - 13 * mm, ry, f"{delta:+d}p")
            c.setFillColor(_risk_color(pct))
            c.setFont('Helvetica-Bold', 7.2)
            c.drawRightString(x + width - 4, ry, f"{pct}%")
            ry -= 8.5 * mm
        return y_top - box_h

    box_bottom = draw_top5_box(
        left_x,
        y,
        col_w,
        'Top 5 evolução da semana',
        top_evolucao,
        mode='delta')
    draw_top5_box(
        right_x,
        y,
        col_w,
        'Top 5 mais atrasados',
        top_atrasados,
        mode='pct')
    y = box_bottom - 8 * mm

    c.setFillColor(FG)
    c.setFont('Helvetica-Bold', 10)
    c.drawString(16 * mm, y, 'Tendência semanal')
    y -= 4 * mm

    chart_h = 44 * mm
    c.setFillColor(SURFACE)
    c.setStrokeColor(BORDER)
    c.roundRect(
        16 * mm,
        y - chart_h,
        w - 32 * mm,
        chart_h,
        3,
        fill=1,
        stroke=1)
    cx0, cy0 = 24 * mm, y - chart_h + 8 * mm
    cw, ch = w - 48 * mm, chart_h - 16 * mm
    c.setStrokeColor(BORDER)
    for frac in (0, 0.25, 0.5, 0.75, 1):
        yy = cy0 + ch * frac
        c.line(cx0, yy, cx0 + cw, yy)
        c.setFillColor(MUTED)
        c.setFont('Helvetica', 6.5)
        c.drawRightString(cx0 - 2, yy - 2, f"{int(frac * 100)}%")

    trend = payload.trend_semanal or []
    trend = sorted(trend, key=lambda r: int(r.get('semana', 0)))[-4:]
    if trend:
        n = len(trend)
        pts = []
        for i, row in enumerate(trend):
            px = cx0 + (cw * i / max(n - 1, 1))
            py = cy0 + ch * (int(row.get('pct', 0)) / 100)
            pts.append(
                (px, py, int(
                    row.get(
                        'semana', 0)), int(
                    row.get(
                        'pct', 0))))
        c.setStrokeColor(BLUE)
        c.setLineWidth(1.6)
        for i in range(len(pts) - 1):
            c.line(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
        for px, py, sem, pct in pts:
            c.setFillColor(BLUE)
            c.circle(px, py, 2.2, stroke=0, fill=1)
            c.setFillColor(FG)
            c.setFont('Helvetica-Bold', 6.8)
            c.drawCentredString(px, cy0 - 4.5 * mm, f"Sem.{sem}")
            c.setFillColor(BLUE)
            c.setFont('Helvetica-Bold', 6.5)
            c.drawCentredString(px, py + 3.5 * mm, f"{pct}%")
    else:
        c.setFillColor(MUTED)
        c.setFont('Helvetica', 8)
        c.drawCentredString(16 * mm + (w - 32 * mm) / 2, y - chart_h / 2,
                            'Adicione payload.trend_semanal para mostrar as últimas 4 semanas.')
    y -= chart_h + 8 * mm

    footer()
    c.showPage()

    # Página 3 - sem movimentação com alertas inteligentes
    page_header('Equipamentos sem movimentação')
    y = h - 20 * mm
    y = mini_kpi_row(y,
                     [('Equipamentos parados',
                       str(total_parados),
                         YELLOW if total_parados else GREEN),
                         ('Departamentos afetados',
                          str(len(deptos_com_parados)),
                          RED if deptos_com_parados else GREEN),
                         ('Maior tempo parado',
                          f"{maior_dias}d" if maior_dias else '0d',
                          _stoppage_color(maior_dias)),
                      ],
                     cols=3)

    y = mini_kpi_row(y, [
        ('Atenção > 7d', str(n_atencao), YELLOW),
        ('Crítico > 14d', str(n_critico), ORANGE),
        ('Urgente > 21d', str(n_urgente), RED),
    ], cols=3)

    c.setFillColor(FG)
    c.setFont('Helvetica-Bold', 9)
    c.drawString(16 * mm, y, 'Resumo por departamento')
    y -= 6 * mm
    c.setFillColor(DARK)
    c.rect(16 * mm, y - 7 * mm, w - 32 * mm, 7 * mm, fill=1, stroke=0)
    c.setFillColor(colors.Color(0.6, 0.65, 0.7))
    c.setFont('Helvetica-Bold', 7.5)
    c.drawString(20 * mm, y - 5 * mm, 'Departamento')
    c.drawString(103 * mm, y - 5 * mm, 'Parados')
    c.drawString(125 * mm, y - 5 * mm, 'Maior tempo')
    c.drawString(147 * mm, y - 5 * mm, 'Status')
    c.drawRightString(w - 18 * mm, y - 5 * mm, 'Impacto')
    y -= 7 * mm
    linhas = deptos_com_parados or deptos
    max_parados = max([int(getattr(d, 'n_parados', 0) or 0)
                      for d in linhas] or [1])
    row_h = 10 * mm
    for i, dept in enumerate(sorted(linhas, key=lambda d: (-int(getattr(
            d, 'n_parados', 0) or 0), -int(getattr(d, 'max_dias_parado', 0) or 0), d.nome))):
        if y - row_h < 18 * mm:
            footer()
            c.showPage()
            page_header('Equipamentos sem movimentação (cont.)')
            y = h - 20 * mm
        n_par = int(getattr(dept, 'n_parados', 0) or 0)
        max_d = int(getattr(dept, 'max_dias_parado', 0) or 0)
        bar_col = _stoppage_color(max_d)
        label = 'Urgente' if max_d > 21 else 'Crítico' if max_d > 14 else 'Atenção' if max_d > 7 else 'Normal'
        c.setFillColor(SURFACE if i % 2 == 0 else WHITE)
        c.rect(16 * mm, y - row_h, w - 32 * mm, row_h, fill=1, stroke=0)
        c.setFillColor(bar_col)
        c.rect(16 * mm, y - row_h, 3, row_h, fill=1, stroke=0)
        c.setFillColor(FG)
        c.setFont('Helvetica-Bold', 8.2)
        c.drawString(20 * mm, y - row_h * 0.38, dept.nome[:34])
        c.setFillColor(MUTED)
        c.setFont('Helvetica', 7.2)
        c.drawString(20 * mm, y - row_h * 0.72,
                     f"{dept.n_equipamentos} equipamentos monitorados")
        c.setFillColor(FG)
        c.setFont('Helvetica-Bold', 8.5)
        c.drawString(103 * mm, y - row_h * 0.5, str(n_par))
        c.drawString(125 * mm, y - row_h * 0.5, f"{max_d}d" if max_d else '—')
        c.setFillColor(bar_col)
        c.setFont('Helvetica-Bold', 7.2)
        c.drawString(147 * mm, y - row_h * 0.5, label)
        pbar(164 * mm, y - row_h + 2.4 * mm, 23 * mm, 5 * mm,
             round((n_par / max(max_parados, 1)) * 100), color=bar_col)
        impacto = f"{round((n_par / max(dept.n_equipamentos, 1)) * 100)}%" if dept.n_equipamentos else '0%'
        c.setFillColor(bar_col)
        c.setFont('Helvetica-Bold', 8.5)
        c.drawRightString(w - 18 * mm, y - row_h * 0.38, impacto)
        c.setStrokeColor(BORDER)
        c.setLineWidth(0.3)
        c.line(16 * mm, y - row_h, w - 16 * mm, y - row_h)
        y -= row_h

    footer()
    c.showPage()

    # Página 4 - heatmap opcional
    heatmap = payload.heatmap_semanal or []
    if heatmap:
        page_header('Heatmap de manutenção por departamento')
        y = h - 20 * mm
        y = section_title('Departamentos × semanas', y)
        semanas = sorted({int(r.get('semana', 0)) for r in heatmap})[-4:]
        dept_names = [d.nome for d in deptos][:12]
        cell_h = 9 * mm
        cell_w = 23 * mm
        table_x = 44 * mm
        for i, sem in enumerate(semanas):
            c.setFillColor(MUTED)
            c.setFont('Helvetica-Bold', 7.2)
            c.drawCentredString(
                table_x +
                i *
                cell_w +
                cell_w /
                2,
                y,
                f"Sem.{sem}")
        y -= 5 * mm
        data = {(str(r.get('departamento', '')), int(r.get('semana', 0))): int(r.get('pct', 0) or 0) for r in heatmap}
        for dept_name in dept_names:
            if y - cell_h < 18 * mm:
                footer()
                c.showPage()
                page_header('Heatmap de manutenção por departamento (cont.)')
                y = h - 20 * mm
            c.setFillColor(FG)
            c.setFont('Helvetica-Bold', 7)
            c.drawString(16 * mm, y - 5.2 * mm, dept_name[:20])
            for i, sem in enumerate(semanas):
                pct = data.get((dept_name, sem), 0)
                fill = _risk_color(pct)
                c.setFillColor(fill)
                c.roundRect(
                    table_x + i * cell_w,
                    y - cell_h,
                    cell_w - 2,
                    cell_h - 1,
                    2,
                    fill=1,
                    stroke=0)
                c.setFillColor(WHITE)
                c.setFont('Helvetica-Bold', 7)
                c.drawCentredString(table_x + i * cell_w +
                                    (cell_w - 2) / 2, y - 5.5 * mm, f"{pct}%")
            y -= cell_h + 1.5 * mm
        footer()
        c.showPage()

    # Página 5+ - Destaques por departamento
    page_header('Destaques por Departamento')
    PAGE_TOP = h - 20 * mm
    PAGE_BOT = 18 * mm
    col_w = (w - 36 * mm) / 2
    col_gap = 4 * mm
    col_x = [16 * mm, 16 * mm + col_w + col_gap]
    y_col = [PAGE_TOP, PAGE_TOP]
    ci = 0

    def dept_block_height(dept: DeptSnapshot) -> float:
        base = 15 * mm
        section_gap = 6.5 * mm
        row_gap = 6.5 * mm
        bottom_pad = 6 * mm
        sections = [min(len(dept.top_criticos), 3), min(
            len(dept.top_melhores), 3), min(len(dept.maiores_evolucoes), 3)]
        active_sections = [n for n in sections if n > 0]
        return base + sum(section_gap + (n * row_gap)
                          for n in active_sections) + bottom_pad

    def draw_dept_block(dept: DeptSnapshot, bx: float, by: float, bh: float):
        dept_pct = real_pct_dept(dept)
        col_dep = _risk_color(dept_pct)
        c.setFillColor(SURFACE)
        c.setStrokeColor(BORDER)
        c.setLineWidth(0.5)
        c.roundRect(bx, by - bh, col_w, bh, 3, fill=1, stroke=1)
        c.setFillColor(col_dep)
        c.rect(bx, by - bh, 3, bh, fill=1, stroke=0)
        c.setFillColor(FG)
        c.setFont('Helvetica-Bold', 8.5)
        c.drawString(bx + 7, by - 6 * mm, dept.nome[:22])
        c.setFillColor(col_dep)
        c.setFont('Helvetica-Bold', 9)
        c.drawRightString(bx + col_w - 4, by - 6 * mm, f"{dept_pct}%")
        c.setStrokeColor(BORDER)
        c.line(bx + 4, by - 9 * mm, bx + col_w - 4, by - 9 * mm)
        iy = by - 11 * mm
        if dept.top_criticos:
            c.setFillColor(RED)
            c.setFont('Helvetica-Bold', 7)
            c.drawString(bx + 6, iy, 'Piores:')
            iy -= 5.5 * mm
            for eq in dept.top_criticos[:3]:
                pct_eq = int(eq.get('pct', 0))
                c.setFillColor(FG)
                c.setFont('Helvetica-Bold', 7.5)
                c.drawString(bx + 8, iy, str(eq.get('frota') or '—')[:7])
                c.setFillColor(MUTED)
                c.setFont('Helvetica', 7)
                c.drawString(bx + 20 * mm, iy,
                             str(eq.get('modelo') or '')[:13])
                pbar(
                    bx + col_w - 26 * mm,
                    iy - 1.5 * mm,
                    18 * mm,
                    3.5 * mm,
                    pct_eq)
                c.setFillColor(_risk_color(pct_eq))
                c.setFont('Helvetica-Bold', 7.5)
                c.drawRightString(bx + col_w - 4, iy, f"{pct_eq}%")
                iy -= 6 * mm
        if dept.top_melhores:
            c.setFillColor(GREEN)
            c.setFont('Helvetica-Bold', 7)
            c.drawString(bx + 6, iy, 'Quase concluídos:')
            iy -= 5.5 * mm
            for eq in dept.top_melhores[:3]:
                pct_eq = int(eq.get('pct', 0))
                c.setFillColor(FG)
                c.setFont('Helvetica-Bold', 7.5)
                c.drawString(bx + 8, iy, str(eq.get('frota') or '—')[:7])
                c.setFillColor(MUTED)
                c.setFont('Helvetica', 7)
                c.drawString(bx + 20 * mm, iy,
                             str(eq.get('modelo') or '')[:13])
                pbar(bx + col_w - 26 * mm, iy - 1.5 * mm, 18 * mm, 3.5 *
                     mm, pct_eq, color=GREEN if pct_eq >= 80 else YELLOW)
                c.setFillColor(GREEN)
                c.setFont('Helvetica-Bold', 7.5)
                c.drawRightString(bx + col_w - 4, iy, f"{pct_eq}%")
                iy -= 6 * mm
        if dept.maiores_evolucoes:
            c.setFillColor(INDIGO)
            c.setFont('Helvetica-Bold', 7)
            c.drawString(bx + 6, iy, 'Evoluções:')
            iy -= 5.5 * mm
            for eq in dept.maiores_evolucoes[:3]:
                pct_eq = int(eq.get('pct', 0))
                delta_v = pct_eq - int(eq.get('pct_anterior', pct_eq))
                c.setFillColor(FG)
                c.setFont('Helvetica-Bold', 7.3)
                c.drawString(bx + 8, iy, str(eq.get('frota') or '—')[:7])
                c.setFillColor(MUTED)
                c.setFont('Helvetica', 6.2)
                c.drawString(bx + 20 * mm, iy, str(eq.get('modelo') or '')[:8])
                bar_x = bx + col_w - 26 * mm
                bar_w = 12 * mm
                delta_x = bx + col_w - 8 * mm
                pct_x = bx + col_w - 4
                pbar(bar_x, iy - 1.5 * mm, bar_w, 3.3 * mm, pct_eq, color=BLUE)
                if delta_v > 0:
                    c.setFillColor(GREEN)
                    c.setFont('Helvetica-Bold', 5.8)
                    c.drawRightString(delta_x, iy, f"+{delta_v}p")
                c.setFillColor(_risk_color(pct_eq))
                c.setFont('Helvetica-Bold', 6.2)
                c.drawRightString(pct_x, iy, f"{pct_eq}%")
                iy -= 6.5 * mm

    for dept in deptos:
        bh = dept_block_height(dept)
        placed = False
        for _ in range(3):
            if y_col[ci] - bh >= PAGE_BOT:
                draw_dept_block(dept, col_x[ci], y_col[ci], bh)
                y_col[ci] -= bh + 3 * mm
                placed = True
                ci = 1 - ci
                break
            next_ci = 1 - ci
            if y_col[next_ci] - bh >= PAGE_BOT:
                ci = next_ci
            else:
                footer()
                c.showPage()
                page_header('Destaques por Departamento (cont.)')
                y_col = [PAGE_TOP, PAGE_TOP]
                ci = 0
        if not placed:
            draw_dept_block(dept, col_x[ci], y_col[ci], bh)
            y_col[ci] -= bh + 3 * mm
            ci = 1 - ci

    footer()
    c.save()
    return buf.getvalue()
