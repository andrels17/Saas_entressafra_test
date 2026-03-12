"""PDF Executivo — visão consolidada para supervisores e diretores.

Conteúdo (compacto, direto):
  Página 1 — Capa: progresso global + semana atual
  Página 2 — Ranking de departamentos (melhor → pior) com barras + alertas
  Página 3 — Destaques: top 3 piores equip. por depto + maiores evoluções
  (sem lista longa de equipamentos)
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

# ── Paleta ────────────────────────────────────────────────────────────────────
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


def _hex(h: str) -> colors.Color:
    try:
        s = h.lstrip("#")
        r, g, b = int(s[0:2], 16)/255, int(s[2:4], 16)/255, int(s[4:6], 16)/255
        return colors.Color(r, g, b)
    except Exception:
        return colors.gray


# ── Payloads ──────────────────────────────────────────────────────────────────

@dataclass
class DeptSnapshot:
    """Resumo de um departamento para o relatório executivo."""
    nome: str
    pct_geral: int
    pct_anterior: int               # semana passada
    n_equipamentos: int
    n_concluidos: int
    n_travados: int
    n_sem_inicio: int
    n_risco_prazo: int
    top_criticos: list[dict]        # [{frota, modelo, pct, status}] top 3 piores
    maiores_evolucoes: list[dict]   # [{frota, modelo, pct, pct_anterior}] top 3


@dataclass
class RelatorioExecutivoPayload:
    tenant_nome: str
    revisao_titulo: str
    semana_atual: int
    semanas_total: int
    pct_global: int                 # média ponderada de todos os deptos
    n_equip_total: int
    n_equip_concluidos: int
    n_alertas_total: int
    departamentos: List[DeptSnapshot]
    primary_color: str = "#FFD100"
    logo_url: str | None = None


# ── Gerador ───────────────────────────────────────────────────────────────────

def build_executive_pdf(payload: RelatorioExecutivoPayload) -> bytes:
    buf = io.BytesIO()
    c   = canvas.Canvas(buf, pagesize=A4)
    w, h = A4

    accent = _hex(payload.primary_color)
    now    = datetime.now().strftime("%d/%m/%Y %H:%M")

    # ── helpers locais ────────────────────────────────────────────────────────
    def footer():
        c.setFillColor(MUTED); c.setFont("Helvetica", 7.5)
        c.drawString(16*mm, 10*mm, f"{payload.tenant_nome} · Relatório Executivo · {now}")
        c.drawRightString(w - 16*mm, 10*mm, f"Semana {payload.semana_atual}/{payload.semanas_total}")

    def new_page(title: str):
        c.setFillColor(DARK)
        c.rect(0, h - 14*mm, w, 14*mm, fill=1, stroke=0)
        c.setFillColor(accent)
        c.rect(0, h - 14*mm, 4, 14*mm, fill=1, stroke=0)
        c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 9)
        c.drawString(10*mm, h - 9*mm, title)
        c.setFillColor(MUTED); c.setFont("Helvetica", 8)
        c.drawRightString(w - 10*mm, h - 9*mm, payload.revisao_titulo)

    def progress_bar(x, y, bw, bh, pct):
        col = _risk_color(pct)
        c.setFillColor(BORDER)
        c.roundRect(x, y, bw, bh, bh/2, fill=1, stroke=0)
        filled = max(bw * min(pct, 100) / 100, bh if pct > 0 else 0)
        if filled > 0:
            c.setFillColor(col)
            c.roundRect(x, y, filled, bh, bh/2, fill=1, stroke=0)

    def section_title(txt: str, y: float) -> float:
        c.setFillColor(accent)
        c.rect(16*mm, y - 0.8*mm, 3, 5*mm, fill=1, stroke=0)
        c.setFillColor(FG); c.setFont("Helvetica-Bold", 10)
        c.drawString(21*mm, y, txt)
        return y - 9*mm

    # ── PÁGINA 1: Capa executiva ──────────────────────────────────────────────
    # Fundo escuro no topo
    c.setFillColor(DARK)
    c.rect(0, h - 72*mm, w, 72*mm, fill=1, stroke=0)
    c.setFillColor(accent)
    c.rect(0, h - 72*mm, w, 3, fill=1, stroke=0)

    c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 22)
    c.drawString(16*mm, h - 22*mm, "Relatório Executivo de Revisão")
    c.setFillColor(colors.Color(0.7, 0.75, 0.8)); c.setFont("Helvetica", 11)
    c.drawString(16*mm, h - 32*mm, payload.revisao_titulo)
    c.drawString(16*mm, h - 40*mm,
                 f"Semana {payload.semana_atual} de {payload.semanas_total}  ·  {payload.tenant_nome}  ·  {now}")

    # Barra de progresso global grande
    bar_y = h - 60*mm
    bar_w = w - 32*mm
    c.setFillColor(colors.Color(0.2, 0.23, 0.28))
    c.roundRect(16*mm, bar_y, bar_w, 8*mm, 4, fill=1, stroke=0)
    filled_w = max(bar_w * payload.pct_global / 100, 8*mm if payload.pct_global > 0 else 0)
    c.setFillColor(_risk_color(payload.pct_global))
    c.roundRect(16*mm, bar_y, filled_w, 8*mm, 4, fill=1, stroke=0)
    c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 11)
    c.drawRightString(w - 16*mm, bar_y - 5*mm, f"{payload.pct_global}% concluído")

    # KPI cards no meio da página
    y_cards = h - 90*mm
    card_w  = (w - 40*mm) / 4
    kpis = [
        ("Departamentos",    str(len(payload.departamentos)),  WHITE),
        ("Equipamentos",     str(payload.n_equip_total),       WHITE),
        ("Concluídos",       str(payload.n_equip_concluidos),  GREEN),
        ("Alertas ativos",   str(payload.n_alertas_total),     RED if payload.n_alertas_total else GREEN),
    ]
    for i, (label, val, col) in enumerate(kpis):
        cx = 16*mm + i * (card_w + 2.5*mm)
        c.setFillColor(colors.Color(0.14, 0.17, 0.22))
        c.roundRect(cx, y_cards - 24*mm, card_w, 24*mm, 4, fill=1, stroke=0)
        c.setFillColor(col); c.setFont("Helvetica-Bold", 18)
        c.drawString(cx + 5, y_cards - 14*mm, val)
        c.setFillColor(MUTED); c.setFont("Helvetica", 8)
        c.drawString(cx + 5, y_cards - 20*mm, label)

    footer(); c.showPage()

    # ── PÁGINA 2: Ranking de departamentos ────────────────────────────────────
    new_page("Ranking de Departamentos")
    y = h - 22*mm

    deptos_sorted = sorted(payload.departamentos, key=lambda d: -d.pct_geral)
    row_h = 20*mm

    for i, dept in enumerate(deptos_sorted):
        if y - row_h < 20*mm:
            footer(); c.showPage()
            new_page("Ranking de Departamentos (cont.)")
            y = h - 22*mm

        # card do departamento
        bg = SURFACE if i % 2 == 0 else WHITE
        c.setFillColor(bg)
        c.roundRect(16*mm, y - row_h, w - 32*mm, row_h, 4, fill=1, stroke=0)

        # borda colorida lateral pelo progresso
        col = _risk_color(dept.pct_geral)
        c.setFillColor(col)
        c.rect(16*mm, y - row_h, 4, row_h, fill=1, stroke=0)

        # posição no ranking
        c.setFillColor(MUTED); c.setFont("Helvetica-Bold", 9)
        c.drawString(24*mm, y - row_h/2, f"#{i+1}")

        # nome
        c.setFillColor(FG); c.setFont("Helvetica-Bold", 10)
        nome_trunc = dept.nome[:28]
        c.drawString(36*mm, y - row_h*0.35, nome_trunc)

        # métricas
        c.setFillColor(MUTED); c.setFont("Helvetica", 8)
        c.drawString(36*mm, y - row_h*0.68,
                     f"{dept.n_equipamentos} equip.  ·  {dept.n_concluidos} concluídos"
                     f"  ·  {dept.n_travados} travados  ·  {dept.n_sem_inicio} sem início")

        # barra de progresso
        bar_x = w/2
        bar_w2 = w - 32*mm - (bar_x - 16*mm) - 20*mm
        progress_bar(bar_x, y - row_h + 6*mm, bar_w2, 6*mm, dept.pct_geral)

        # %
        c.setFillColor(col); c.setFont("Helvetica-Bold", 13)
        c.drawRightString(w - 18*mm, y - row_h*0.42, f"{dept.pct_geral}%")

        # delta semana
        delta = dept.pct_geral - dept.pct_anterior
        if delta != 0:
            d_col = GREEN if delta > 0 else RED
            d_str = f"+{delta}p.p." if delta > 0 else f"{delta}p.p."
            c.setFillColor(d_col); c.setFont("Helvetica-Bold", 7.5)
            c.drawRightString(w - 18*mm, y - row_h*0.72, d_str)

        # borda inferior
        c.setStrokeColor(BORDER); c.setLineWidth(0.4)
        c.line(16*mm, y - row_h, w - 16*mm, y - row_h)
        y -= row_h

    footer(); c.showPage()

    # ── PÁGINA 3: Destaques por departamento ──────────────────────────────────
    new_page("Destaques por Departamento")
    y = h - 22*mm

    for dept in deptos_sorted:
        block_h = 10*mm
        has_criticos   = bool(dept.top_criticos)
        has_evolucoes  = bool(dept.maiores_evolucoes)
        n_items = max(len(dept.top_criticos), 1) + max(len(dept.maiores_evolucoes), 0)
        block_h = 12*mm + n_items * 7*mm + 4*mm

        if y - block_h < 20*mm:
            footer(); c.showPage()
            new_page("Destaques por Departamento (cont.)")
            y = h - 22*mm

        # cabeçalho do bloco
        col = _risk_color(dept.pct_geral)
        c.setFillColor(SURFACE)
        c.roundRect(16*mm, y - block_h, w - 32*mm, block_h, 4, fill=1, stroke=0)
        c.setFillColor(col)
        c.rect(16*mm, y - block_h, 4, block_h, fill=1, stroke=0)

        c.setFillColor(FG); c.setFont("Helvetica-Bold", 9.5)
        c.drawString(24*mm, y - 7*mm, dept.nome)
        c.setFillColor(col); c.setFont("Helvetica-Bold", 9.5)
        c.drawRightString(w - 20*mm, y - 7*mm, f"{dept.pct_geral}%")

        iy = y - 13*mm

        # top críticos
        if has_criticos:
            c.setFillColor(MUTED); c.setFont("Helvetica-Bold", 7.5)
            c.drawString(24*mm, iy, "⚠ Piores progresso:")
            iy -= 6*mm
            for eq in dept.top_criticos[:3]:
                pct = int(eq.get("pct", 0))
                frota  = str(eq.get("frota") or "—")[:8]
                modelo = str(eq.get("modelo") or "")[:14]
                eq_col = _risk_color(pct)

                c.setFillColor(FG); c.setFont("Helvetica-Bold", 8)
                c.drawString(28*mm, iy, frota)
                c.setFillColor(MUTED); c.setFont("Helvetica", 7.5)
                c.drawString(40*mm, iy, modelo)

                bx = 80*mm
                bw3 = 50*mm
                progress_bar(bx, iy - 1.5*mm, bw3, 4*mm, pct)
                c.setFillColor(eq_col); c.setFont("Helvetica-Bold", 7.5)
                c.drawRightString(w - 20*mm, iy, f"{pct}%")
                iy -= 6.5*mm

        # maiores evoluções
        if has_evolucoes:
            c.setFillColor(GREEN); c.setFont("Helvetica-Bold", 7.5)
            c.drawString(24*mm, iy, "📈 Maiores evoluções:")
            iy -= 6*mm
            for eq in dept.maiores_evolucoes[:3]:
                pct      = int(eq.get("pct", 0))
                pct_ant  = int(eq.get("pct_anterior", pct))
                delta    = pct - pct_ant
                frota    = str(eq.get("frota") or "—")[:8]
                modelo   = str(eq.get("modelo") or "")[:14]

                c.setFillColor(FG); c.setFont("Helvetica-Bold", 8)
                c.drawString(28*mm, iy, frota)
                c.setFillColor(MUTED); c.setFont("Helvetica", 7.5)
                c.drawString(40*mm, iy, modelo)

                bx = 80*mm; bw3 = 50*mm
                progress_bar(bx, iy - 1.5*mm, bw3, 4*mm, pct)
                c.setFillColor(_risk_color(pct)); c.setFont("Helvetica-Bold", 7.5)
                c.drawRightString(w - 34*mm, iy, f"{pct}%")
                if delta > 0:
                    c.setFillColor(GREEN); c.setFont("Helvetica-Bold", 7)
                    c.drawRightString(w - 20*mm, iy, f"+{delta}p.p.")
                iy -= 6.5*mm

        c.setStrokeColor(BORDER); c.setLineWidth(0.4)
        c.line(16*mm, y - block_h, w - 16*mm, y - block_h)
        y -= block_h + 3*mm

    footer(); c.showPage()
    c.save()
    return buf.getvalue()
