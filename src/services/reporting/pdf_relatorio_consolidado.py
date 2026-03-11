"""PDF Consolidado — relatório geral para admins.

Conteúdo:
  Página 1  — Capa executiva: KPIs totais de todos os departamentos
  Página 2  — Ranking de departamentos (tabela + barras)
  Página N  — Uma seção por departamento (resumo compacto: KPIs + críticos)

Usa o mesmo sistema visual de pdf_relatorio_semanal.py.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime
from typing import List

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

# ── paleta compartilhada ──────────────────────────────────────────────────────
FG      = colors.Color(0.10, 0.12, 0.16)
MUTED   = colors.Color(0.42, 0.46, 0.52)
SURFACE = colors.Color(0.96, 0.97, 0.98)
BORDER  = colors.Color(0.88, 0.90, 0.93)
GREEN   = colors.HexColor("#12B76A")
YELLOW  = colors.HexColor("#F59E0B")
RED     = colors.HexColor("#EF4444")
DARK    = colors.HexColor("#111827")
WHITE   = colors.white


def _risk_color(pct: int) -> colors.Color:
    if pct >= 80: return GREEN
    if pct >= 50: return YELLOW
    return RED


def _hex(h: str, fallback=colors.gray) -> colors.Color:
    try:
        s = h.lstrip("#")
        r, g, b = int(s[0:2], 16) / 255, int(s[2:4], 16) / 255, int(s[4:6], 16) / 255
        return colors.Color(r, g, b)
    except Exception:
        return fallback


# ── Payload ───────────────────────────────────────────────────────────────────

@dataclass
class DeptSummary:
    """Resumo de um departamento para o relatório consolidado."""
    nome: str
    pct_geral: int
    n_equipamentos: int
    n_concluidos: int
    n_travados: int
    n_sem_inicio: int
    n_parados: int
    n_risco_prazo: int
    pct_semana_anterior: int
    pct_semana_atual: int
    criticos: list = field(default_factory=list)   # lista de EquipamentoCritico


@dataclass
class RelatorioConsolidadoPayload:
    tenant_nome: str
    revisao_titulo: str
    semana_atual: int
    semanas_total: int
    departamentos: List[DeptSummary]
    primary_color: str = "#FFD100"
    logo_url: str | None = None

    @property
    def pct_geral(self) -> int:
        if not self.departamentos:
            return 0
        return round(sum(d.pct_geral for d in self.departamentos) / len(self.departamentos))

    @property
    def n_equipamentos_total(self) -> int:
        return sum(d.n_equipamentos for d in self.departamentos)

    @property
    def n_alertas_total(self) -> int:
        return sum(d.n_travados + d.n_risco_prazo for d in self.departamentos)

    @property
    def n_criticos_total(self) -> int:
        return sum(len(d.criticos) for d in self.departamentos)


# ── Gerador ───────────────────────────────────────────────────────────────────

def build_consolidated_pdf(payload: RelatorioConsolidadoPayload) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    PRIMARY = _hex(payload.primary_color)
    page_no = [0]

    # ── helpers (idênticos ao pdf_relatorio_semanal) ──────────────────────────
    def new_page(title: str, subtitle: str = ""):
        page_no[0] += 1
        c.setFillColor(WHITE); c.rect(0, 0, w, h, fill=1, stroke=0)
        c.setFillColor(DARK);  c.rect(0, h - 26*mm, w, 26*mm, fill=1, stroke=0)
        c.setFillColor(PRIMARY); c.rect(0, h - 26*mm, w, 2.5, fill=1, stroke=0)
        c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 14)
        c.drawString(16*mm, h - 14*mm, payload.tenant_nome)
        c.setFont("Helvetica", 9)
        c.setFillColor(colors.Color(1, 1, 1, .6))
        period = f"{payload.revisao_titulo}  ·  Semana {payload.semana_atual}/{payload.semanas_total}"
        c.drawString(16*mm, h - 20*mm, period)
        c.setFillColor(FG); c.setFont("Helvetica-Bold", 15)
        c.drawString(16*mm, h - 38*mm, title)
        if subtitle:
            c.setFillColor(MUTED); c.setFont("Helvetica", 10)
            c.drawString(16*mm, h - 45*mm, subtitle)
        c.setStrokeColor(BORDER); c.setLineWidth(0.8)
        c.line(16*mm, h - 48*mm, w - 16*mm, h - 48*mm)

    def footer():
        c.setStrokeColor(BORDER); c.setLineWidth(0.8)
        c.line(16*mm, 14*mm, w - 16*mm, 14*mm)
        c.setFillColor(MUTED); c.setFont("Helvetica", 8)
        c.drawString(16*mm, 9*mm, "Relatório Consolidado — gerado automaticamente.")
        now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
        c.drawRightString(w - 16*mm, 9*mm, f"{now_str}  ·  pág. {page_no[0]}")

    def section_title(txt: str, y: float) -> float:
        c.setFillColor(FG); c.setFont("Helvetica-Bold", 11)
        c.drawString(16*mm, y, txt)
        c.setStrokeColor(BORDER); c.setLineWidth(0.5)
        c.line(16*mm, y - 2*mm, w - 16*mm, y - 2*mm)
        return y - 8*mm

    def kpi_row(items, top_y: float, card_h: float = 20*mm) -> float:
        n = len(items)
        gap = 4*mm
        card_w = (w - 32*mm - gap * (n - 1)) / n
        x0 = 16*mm
        for i, (label, val, val_color) in enumerate(items):
            cx = x0 + i * (card_w + gap)
            cy = top_y - card_h
            c.setFillColor(SURFACE); c.setStrokeColor(BORDER); c.setLineWidth(0.7)
            c.roundRect(cx, cy, card_w, card_h, 5, fill=1, stroke=1)
            c.setFillColor(PRIMARY); c.rect(cx, top_y - 2.5, card_w, 2.5, fill=1, stroke=0)
            c.setFillColor(MUTED); c.setFont("Helvetica", 8)
            c.drawString(cx + 6, top_y - 8*mm, label)
            c.setFillColor(val_color); c.setFont("Helvetica-Bold", 18)
            c.drawString(cx + 6, cy + 5*mm, str(val))
        return top_y - card_h - 8*mm

    def progress_bar(x, y, bar_w, bar_h, pct):
        pct_c = max(0, min(100, pct))
        c.setFillColor(BORDER); c.roundRect(x, y, bar_w, bar_h, 2, fill=1, stroke=0)
        if pct_c > 0:
            c.setFillColor(_risk_color(pct_c))
            c.roundRect(x, y, bar_w * pct_c / 100, bar_h, 2, fill=1, stroke=0)

    def platypus_table(rows, col_widths, top_y, header_color=DARK) -> float:
        tbl = Table(rows, colWidths=col_widths)
        style = [
            ("BACKGROUND",    (0, 0), (-1, 0), header_color),
            ("TEXTCOLOR",     (0, 0), (-1, 0), WHITE),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, 0), 8.5),
            ("ALIGN",         (0, 0), (-1, 0), "CENTER"),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("FONTSIZE",      (0, 1), (-1, -1), 8),
            ("GRID",          (0, 0), (-1, -1), 0.3, BORDER),
            ("LEFTPADDING",   (0, 0), (-1, -1), 5),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ALIGN",         (1, 1), (-1, -1), "CENTER"),
        ]
        for r in range(1, len(rows)):
            if r % 2 == 0:
                style.append(("BACKGROUND", (0, r), (-1, r), SURFACE))
        tbl.setStyle(TableStyle(style))
        tw, th = tbl.wrapOn(c, w - 32*mm, h)
        draw_y = top_y - th
        tbl.drawOn(c, 16*mm, draw_y)
        return draw_y - 6*mm

    # ── PÁGINA 1: Capa executiva ──────────────────────────────────────────────
    new_page("Relatório Consolidado", "Visão geral de todos os departamentos")
    y = h - 52*mm

    pct_color = _risk_color(payload.pct_geral)
    y = kpi_row([
        ("Progresso geral",      f"{payload.pct_geral}%",          pct_color),
        ("Departamentos",         str(len(payload.departamentos)),   FG),
        ("Equipamentos totais",   str(payload.n_equipamentos_total), FG),
        ("Alertas críticos",      str(payload.n_alertas_total),
         RED if payload.n_alertas_total else GREEN),
    ], top_y=y)

    # Barra geral
    y -= 2*mm
    c.setFillColor(FG); c.setFont("Helvetica-Bold", 10)
    c.drawString(16*mm, y, "Progresso médio geral")
    y -= 6*mm
    progress_bar(16*mm, y, w - 32*mm, 8*mm, payload.pct_geral)
    c.setFillColor(pct_color); c.setFont("Helvetica-Bold", 9)
    c.drawRightString(w - 16*mm, y + 9*mm, f"{payload.pct_geral}%")
    y -= 16*mm

    # Mini ranking visual na capa
    y = section_title("Progresso por departamento", y)
    label_w = 50*mm
    bar_avail = w - 32*mm - label_w - 16*mm
    row_h = 7.5*mm
    depts_sorted = sorted(payload.departamentos, key=lambda d: d.pct_geral, reverse=True)
    for dept in depts_sorted:
        if y < 30*mm:
            break
        c.setFillColor(FG); c.setFont("Helvetica-Bold", 8.5)
        c.drawString(16*mm, y - row_h / 2, dept.nome[:30])
        progress_bar(16*mm + label_w, y - row_h + 1.5*mm, bar_avail, row_h - 3*mm, dept.pct_geral)
        c.setFillColor(_risk_color(dept.pct_geral)); c.setFont("Helvetica-Bold", 8.5)
        c.drawRightString(w - 16*mm, y - row_h / 2, f"{dept.pct_geral}%")
        y -= row_h + 1.5*mm

    footer(); c.showPage()

    # ── PÁGINA 2: Ranking e comparativo ──────────────────────────────────────
    new_page("Ranking de Departamentos")
    y = h - 52*mm

    y = section_title("Tabela comparativa — semana anterior vs. atual", y)
    rows = [["Departamento", "% S-1", "% Atual", "Variação", "Equip.", "Alertas"]]
    for dept in depts_sorted:
        delta = dept.pct_semana_atual - dept.pct_semana_anterior
        delta_str = f"+{delta}p.p." if delta >= 0 else f"{delta}p.p."
        n_alertas = dept.n_travados + dept.n_risco_prazo
        rows.append([
            dept.nome[:28],
            f"{dept.pct_semana_anterior}%",
            f"{dept.pct_semana_atual}%",
            delta_str,
            str(dept.n_equipamentos),
            str(n_alertas) if n_alertas else "—",
        ])
    pw = w - 32*mm
    y = platypus_table(rows, [pw*0.32, pw*0.12, pw*0.12, pw*0.14, pw*0.14, pw*0.16], y)

    # Resumo de alertas por tipo entre todos os departamentos
    y -= 4*mm
    y = section_title("Alertas consolidados por departamento", y)
    rows2 = [["Departamento", "🚫 Travados", "⬜ Sem início", "⏸ Parados", "⚠️ Risco prazo"]]
    has_alerts = False
    for dept in depts_sorted:
        total_a = dept.n_travados + dept.n_sem_inicio + dept.n_parados + dept.n_risco_prazo
        if total_a == 0:
            continue
        has_alerts = True
        rows2.append([
            dept.nome[:28],
            str(dept.n_travados)   or "—",
            str(dept.n_sem_inicio) or "—",
            str(dept.n_parados)    or "—",
            str(dept.n_risco_prazo)or "—",
        ])
    if has_alerts:
        y = platypus_table(rows2, [pw*0.36, pw*0.16, pw*0.16, pw*0.16, pw*0.16], y)
    else:
        c.setFillColor(GREEN); c.setFont("Helvetica-Bold", 10)
        c.drawString(16*mm, y - 8*mm, "✓ Nenhum alerta ativo em todos os departamentos.")

    footer(); c.showPage()

    # ── PÁGINAS 3+: Uma seção compacta por departamento ───────────────────────
    for dept in depts_sorted:
        new_page(dept.nome, f"Detalhe do departamento · Semana {payload.semana_atual}")
        y = h - 52*mm

        # KPIs do departamento
        n_alertas_dept = dept.n_travados + dept.n_risco_prazo
        y = kpi_row([
            ("Progresso",         f"{dept.pct_geral}%",      _risk_color(dept.pct_geral)),
            ("Equipamentos",      str(dept.n_equipamentos),   FG),
            ("100% concluídos",   str(dept.n_concluidos),     GREEN),
            ("Alertas ativos",    str(n_alertas_dept),
             RED if n_alertas_dept else GREEN),
        ], top_y=y, card_h=18*mm)

        # Comparativo S-1 vs S atual
        delta = dept.pct_semana_atual - dept.pct_semana_anterior
        delta_str = f"+{delta}p.p." if delta >= 0 else f"{delta}p.p."
        delta_col = GREEN if delta >= 0 else RED
        box_w = (w - 32*mm - 8*mm) / 3
        bx = 16*mm
        for label, val, col in [
            ("Semana anterior", f"{dept.pct_semana_anterior}%", _risk_color(dept.pct_semana_anterior)),
            ("Semana atual",    f"{dept.pct_semana_atual}%",    _risk_color(dept.pct_semana_atual)),
            ("Variação",        delta_str,                       delta_col),
        ]:
            c.setFillColor(SURFACE); c.setStrokeColor(BORDER); c.setLineWidth(0.7)
            c.roundRect(bx, y - 16*mm, box_w, 16*mm, 5, fill=1, stroke=1)
            c.setFillColor(MUTED); c.setFont("Helvetica", 8)
            c.drawString(bx + 6, y - 6*mm, label)
            c.setFillColor(col); c.setFont("Helvetica-Bold", 16)
            c.drawString(bx + 6, y - 14*mm, val)
            bx += box_w + 4*mm
        y -= 24*mm

        # Equipamentos críticos (compacto)
        if dept.criticos:
            y = section_title(f"Equipamentos críticos ({len(dept.criticos)})", y)
            status_labels = {"zero": "0% — Sem início", "travado": "Travado", "atrasado": "Atrasado"}
            pw = w - 32*mm
            rows_eq = [["Frota", "Modelo", "Grupo", "%", "Situação"]]
            for eq in dept.criticos[:8]:
                rows_eq.append([
                    eq.frota,
                    (eq.modelo or "—")[:18],
                    (eq.grupo  or "—")[:16],
                    f"{eq.pct}%",
                    status_labels.get(eq.status, eq.status),
                ])
            y = platypus_table(rows_eq,
                [pw*0.12, pw*0.26, pw*0.22, pw*0.12, pw*0.28], y)
        else:
            c.setFillColor(GREEN); c.setFont("Helvetica-Bold", 10)
            c.drawString(16*mm, y - 8*mm, "✓ Nenhum equipamento crítico neste departamento.")
            y -= 16*mm

        # Barra de alertas
        y -= 2*mm
        alert_items = [
            ("🚫 Travados",     dept.n_travados,    RED),
            ("⬜ Sem início",   dept.n_sem_inicio,  MUTED),
            ("⏸ Parados",      dept.n_parados,     YELLOW),
            ("⚠️ Risco prazo",  dept.n_risco_prazo, RED),
        ]
        n_with_alerts = sum(1 for _, n, _ in alert_items if n > 0)
        if n_with_alerts:
            y = section_title("Alertas", y)
            for label, count, col in alert_items:
                if count == 0:
                    continue
                if y < 30*mm:
                    break
                c.setFillColor(col if count else MUTED); c.setFont("Helvetica-Bold", 9)
                c.drawString(16*mm, y, f"{label}: {count}")
                y -= 6*mm

        footer(); c.showPage()

    c.save()
    return buf.getvalue()
