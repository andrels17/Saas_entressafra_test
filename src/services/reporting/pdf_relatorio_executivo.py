"""PDF Executivo — visão consolidada para supervisores e diretores.

Conteúdo:
  Página 1 — Capa densa: KPIs globais + distribuição por faixa + ranking completo
  Página(s) 2+ — Destaques por departamento: piores + maiores evoluções
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime
from src.utils.timezone import fmt_brt
from typing import List

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

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
    top_criticos: list[dict]       # [{frota, modelo, pct, status}] — menor %
    top_melhores: list[dict]       # [{frota, modelo, pct, pct_anterior}] — maior %, quase concluídos
    maiores_evolucoes: list[dict]  # [{frota, modelo, pct, pct_anterior}] — maior delta semana
    n_parados: int = 0
    max_dias_parado: int = 0
    _done_steps: int = 0           # para cálculo ponderado do pct_global
    _expected_steps: int = 0       # para cálculo ponderado do pct_global


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


def build_executive_pdf(payload: RelatorioExecutivoPayload) -> bytes:
    buf = io.BytesIO()
    c   = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    accent = _hex(payload.primary_color)
    now    = fmt_brt()

    # ── helpers ───────────────────────────────────────────────────────────────
    def footer():
        c.setFillColor(MUTED); c.setFont("Helvetica", 7.5)
        c.drawString(16*mm, 10*mm, f"{payload.tenant_nome} · Relatório Executivo · {now}")
        c.drawRightString(w - 16*mm, 10*mm, f"Semana {payload.semana_atual}/{payload.semanas_total}")

    def page_header(title: str):
        c.setFillColor(DARK)
        c.rect(0, h - 13*mm, w, 13*mm, fill=1, stroke=0)
        c.setFillColor(accent)
        c.rect(0, h - 13*mm, 4, 13*mm, fill=1, stroke=0)
        c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 9)
        c.drawString(10*mm, h - 8.5*mm, title)
        c.setFillColor(colors.Color(0.6, 0.65, 0.7)); c.setFont("Helvetica", 8)
        c.drawRightString(w - 10*mm, h - 8.5*mm, payload.revisao_titulo)

    def pbar(x, y, bw, bh, pct):
        col = _risk_color(pct)
        c.setFillColor(BORDER)
        c.roundRect(x, y, bw, bh, bh/2, fill=1, stroke=0)
        fw = max(bw * min(pct, 100) / 100, bh if pct > 0 else 0)
        if fw > 0:
            c.setFillColor(col)
            c.roundRect(x, y, fw, bh, bh/2, fill=1, stroke=0)

    def delta_str(d: int) -> str:
        return f"+{d}p.p." if d > 0 else f"{d}p.p."

    def section_title(txt: str, y: float) -> float:
        c.setFillColor(accent)
        c.rect(16*mm, y - 0.8*mm, 3, 5*mm, fill=1, stroke=0)
        c.setFillColor(FG); c.setFont("Helvetica-Bold", 10)
        c.drawString(21*mm, y, txt)
        return y - 9*mm

    deptos = sorted(payload.departamentos, key=lambda d: -d.pct_geral)
    n_verde    = sum(1 for d in deptos if d.pct_geral >= 80)
    n_amarelo  = sum(1 for d in deptos if 50 <= d.pct_geral < 80)
    n_vermelho = sum(1 for d in deptos if d.pct_geral < 50)
    n_deptos   = len(deptos)

    # ══════════════════════════════════════════════════════════════════════════
    # PÁGINA 1 — Capa densa
    # ══════════════════════════════════════════════════════════════════════════
    # Faixa topo escura
    c.setFillColor(DARK)
    c.rect(0, h - 38*mm, w, 38*mm, fill=1, stroke=0)
    c.setFillColor(accent)
    c.rect(0, h - 38*mm, w, 2, fill=1, stroke=0)

    c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 18)
    c.drawString(16*mm, h - 14*mm, "Relatório Executivo de Revisão")
    c.setFillColor(colors.Color(0.65, 0.70, 0.78)); c.setFont("Helvetica", 10)
    c.drawString(16*mm, h - 22*mm, f"{payload.revisao_titulo}  ·  Semana {payload.semana_atual} de {payload.semanas_total}  ·  {payload.tenant_nome}  ·  {now}")

    # Barra global
    c.setFillColor(colors.Color(0.18, 0.21, 0.27))
    c.roundRect(16*mm, h - 34*mm, w - 32*mm, 7*mm, 3.5, fill=1, stroke=0)
    fw = max((w - 32*mm) * payload.pct_global / 100, 7*mm if payload.pct_global > 0 else 0)
    c.setFillColor(_risk_color(payload.pct_global))
    c.roundRect(16*mm, h - 34*mm, fw, 7*mm, 3.5, fill=1, stroke=0)
    c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 9)
    c.drawRightString(w - 18*mm, h - 31*mm, f"{payload.pct_global}% concluído")

    # ── KPI strip (4 cards horizontais compactos) ──────────────────────────
    y_kpi = h - 38*mm - 2*mm
    kpi_h = 16*mm
    kpi_w = (w - 36*mm) / 4
    kpis = [
        ("Departamentos",  str(n_deptos),                     FG),
        ("Equipamentos",   str(payload.n_equip_total),         FG),
        ("Concluídos",     str(payload.n_equip_concluidos),    GREEN),
        ("Alertas",        str(payload.n_alertas_total),       RED if payload.n_alertas_total else GREEN),
    ]
    for i, (lbl, val, col) in enumerate(kpis):
        cx = 16*mm + i * (kpi_w + 1.3*mm)
        c.setFillColor(SURFACE); c.setStrokeColor(BORDER); c.setLineWidth(0.6)
        c.roundRect(cx, y_kpi - kpi_h, kpi_w, kpi_h, 3, fill=1, stroke=1)
        c.setFillColor(col); c.setFont("Helvetica-Bold", 16)
        c.drawString(cx + 4, y_kpi - 10*mm, val)
        c.setFillColor(MUTED); c.setFont("Helvetica", 7.5)
        c.drawString(cx + 4, y_kpi - 14*mm, lbl)

    # ── Faixas de progresso (3 badges inline) ─────────────────────────────
    y_faixas = y_kpi - kpi_h - 5*mm
    badge_data = [
        (f"✅  {n_verde} em dia  (≥80%)",    GREEN),
        (f"⚠  {n_amarelo} atenção  (50–79%)", YELLOW),
        (f"🔴  {n_vermelho} críticos  (<50%)",  RED),
    ]
    bw3 = (w - 36*mm) / 3
    for i, (txt, col) in enumerate(badge_data):
        bx = 16*mm + i * (bw3 + 2*mm)
        c.setFillColor(col)
        c.roundRect(bx, y_faixas - 7*mm, bw3, 7*mm, 3.5, fill=1, stroke=0)
        c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 8)
        c.drawString(bx + 5, y_faixas - 4.5*mm, txt)

    # ── Ranking completo de departamentos ─────────────────────────────────
    y = y_faixas - 7*mm - 6*mm
    row_h = 11*mm

    # Cabeçalho da tabela
    c.setFillColor(DARK)
    c.rect(16*mm, y - 7*mm, w - 32*mm, 7*mm, fill=1, stroke=0)
    c.setFillColor(colors.Color(0.6, 0.65, 0.7)); c.setFont("Helvetica-Bold", 7.5)
    c.drawString(22*mm, y - 5*mm, "#  Departamento")
    c.drawString(w/2 + 2*mm, y - 5*mm, "Progresso")
    c.drawRightString(w - 18*mm, y - 5*mm, "%")
    y -= 7*mm

    for i, dept in enumerate(deptos):
        if y - row_h < 18*mm:
            footer(); c.showPage()
            page_header("Ranking de Departamentos (cont.)")
            y = h - 20*mm

        bg = SURFACE if i % 2 == 0 else WHITE
        c.setFillColor(bg)
        c.rect(16*mm, y - row_h, w - 32*mm, row_h, fill=1, stroke=0)

        col = _risk_color(dept.pct_geral)
        c.setFillColor(col)
        c.rect(16*mm, y - row_h, 3, row_h, fill=1, stroke=0)

        # posição
        c.setFillColor(MUTED); c.setFont("Helvetica", 8)
        c.drawString(22*mm, y - row_h/2, f"#{i+1}")

        # nome
        c.setFillColor(FG); c.setFont("Helvetica-Bold", 8.5)
        c.drawString(32*mm, y - row_h*0.38, dept.nome[:30])

        # métricas pequenas
        c.setFillColor(MUTED); c.setFont("Helvetica", 7)
        c.drawString(32*mm, y - row_h*0.72,
                     f"{dept.n_equipamentos} equip · {dept.n_concluidos} OK"
                     f" · {dept.n_travados}⛔ · {dept.n_sem_inicio} sem início")

        # barra
        bar_x = w/2
        bar_w = w - 32*mm - (bar_x - 16*mm) - 18*mm
        pbar(bar_x, y - row_h + 3*mm, bar_w, 5*mm, dept.pct_geral)

        # %
        c.setFillColor(col); c.setFont("Helvetica-Bold", 9)
        c.drawRightString(w - 18*mm, y - row_h*0.38, f"{dept.pct_geral}%")

        # delta
        delta = dept.pct_geral - dept.pct_anterior
        if delta != 0:
            d_col = GREEN if delta > 0 else RED
            c.setFillColor(d_col); c.setFont("Helvetica-Bold", 7)
            c.drawRightString(w - 18*mm, y - row_h*0.75, delta_str(delta))

        c.setStrokeColor(BORDER); c.setLineWidth(0.3)
        c.line(16*mm, y - row_h, w - 16*mm, y - row_h)
        y -= row_h

    footer(); c.showPage()

    # ══════════════════════════════════════════════════════════════════════════
    # PÁGINA 2 — Equipamentos sem movimentação por departamento
    # ══════════════════════════════════════════════════════════════════════════
    page_header("Equipamentos sem movimentação")
    y = h - 20*mm

    total_parados = sum(int(getattr(d, "n_parados", 0) or 0) for d in deptos)
    deptos_com_parados = [d for d in deptos if int(getattr(d, "n_parados", 0) or 0) > 0]
    maior_dias = max([int(getattr(d, "max_dias_parado", 0) or 0) for d in deptos] or [0])

    card_h = 16*mm
    card_w = (w - 36*mm) / 3
    for i, (lbl, val, col) in enumerate([
        ("Equipamentos parados", str(total_parados), YELLOW if total_parados else GREEN),
        ("Departamentos afetados", str(len(deptos_com_parados)), RED if deptos_com_parados else GREEN),
        ("Maior tempo parado", f"{maior_dias}d" if maior_dias else "0d", RED if maior_dias >= 14 else YELLOW),
    ]):
        cx = 16*mm + i * (card_w + 2*mm)
        c.setFillColor(SURFACE); c.setStrokeColor(BORDER); c.setLineWidth(0.6)
        c.roundRect(cx, y - card_h, card_w, card_h, 3, fill=1, stroke=1)
        c.setFillColor(col); c.setFont("Helvetica-Bold", 16)
        c.drawString(cx + 4, y - 10*mm, val)
        c.setFillColor(MUTED); c.setFont("Helvetica", 7.5)
        c.drawString(cx + 4, y - 14*mm, lbl)
    y -= card_h + 7*mm

    c.setFillColor(FG); c.setFont("Helvetica-Bold", 9)
    c.drawString(16*mm, y, "Resumo por departamento")
    y -= 6*mm

    c.setFillColor(DARK)
    c.rect(16*mm, y - 7*mm, w - 32*mm, 7*mm, fill=1, stroke=0)
    c.setFillColor(colors.Color(0.6, 0.65, 0.7)); c.setFont("Helvetica-Bold", 7.5)
    c.drawString(20*mm, y - 5*mm, "Departamento")
    c.drawString(105*mm, y - 5*mm, "Parados")
    c.drawString(128*mm, y - 5*mm, "Maior tempo")
    c.drawRightString(w - 18*mm, y - 5*mm, "Impacto")
    y -= 7*mm

    linhas = deptos_com_parados or deptos
    row_h = 10*mm
    max_parados = max([int(getattr(d, "n_parados", 0) or 0) for d in linhas] or [1])
    for i, dept in enumerate(sorted(linhas, key=lambda d: (-int(getattr(d, "n_parados", 0) or 0), -int(getattr(d, "max_dias_parado", 0) or 0), d.nome))):
        if y - row_h < 18*mm:
            footer(); c.showPage()
            page_header("Equipamentos sem movimentação (cont.)")
            y = h - 20*mm
        n_par = int(getattr(dept, "n_parados", 0) or 0)
        max_d = int(getattr(dept, "max_dias_parado", 0) or 0)
        c.setFillColor(SURFACE if i % 2 == 0 else WHITE)
        c.rect(16*mm, y - row_h, w - 32*mm, row_h, fill=1, stroke=0)
        bar_col = RED if max_d >= 14 else (YELLOW if n_par else GREEN)
        c.setFillColor(bar_col); c.rect(16*mm, y - row_h, 3, row_h, fill=1, stroke=0)
        c.setFillColor(FG); c.setFont("Helvetica-Bold", 8.2)
        c.drawString(20*mm, y - row_h*0.38, dept.nome[:34])
        c.setFillColor(MUTED); c.setFont("Helvetica", 7.2)
        c.drawString(20*mm, y - row_h*0.72, f"{dept.n_equipamentos} equipamentos monitorados")
        c.setFillColor(FG); c.setFont("Helvetica-Bold", 8.5)
        c.drawString(105*mm, y - row_h*0.5, str(n_par))
        c.drawString(128*mm, y - row_h*0.5, f"{max_d}d" if max_d else "—")
        pbar(145*mm, y - row_h + 2.4*mm, 42*mm, 5*mm, round((n_par / max(max_parados, 1)) * 100))
        impacto = f"{round((n_par / max(dept.n_equipamentos, 1)) * 100)}%" if dept.n_equipamentos else "0%"
        c.setFillColor(bar_col); c.setFont("Helvetica-Bold", 8.5)
        c.drawRightString(w - 18*mm, y - row_h*0.38, impacto)
        c.setStrokeColor(BORDER); c.setLineWidth(0.3)
        c.line(16*mm, y - row_h, w - 16*mm, y - row_h)
        y -= row_h

    footer(); c.showPage()

    # ══════════════════════════════════════════════════════════════════════════
    # PÁGINA 3+ — Destaques por departamento (2 colunas)
    # ══════════════════════════════════════════════════════════════════════════
    page_header("Destaques por Departamento")
    y = h - 20*mm
    col_w  = (w - 36*mm) / 2
    col_gap = 4*mm
    col_x  = [16*mm, 16*mm + col_w + col_gap]
    ci     = 0   # coluna atual (0=esquerda, 1=direita)

    # ══════════════════════════════════════════════════════════════════════════
    # PÁGINA 2+ — Destaques por departamento (2 colunas com y independente)
    # ══════════════════════════════════════════════════════════════════════════
    page_header("Destaques por Departamento")
    PAGE_TOP = h - 20*mm
    PAGE_BOT = 18*mm
    col_w    = (w - 36*mm) / 2
    col_gap  = 4*mm
    col_x    = [16*mm, 16*mm + col_w + col_gap]

    # y independente por coluna
    y_col = [PAGE_TOP, PAGE_TOP]
    ci    = 0

    def dept_block_height(dept: DeptSnapshot) -> float:
        items = min(len(dept.top_criticos), 3) + min(len(dept.top_melhores), 3) + min(len(dept.maiores_evolucoes), 3)
        return 14*mm + items * 6.5*mm + 3*mm

    def draw_dept_block(dept: DeptSnapshot, bx: float, by: float, bh: float):
        col_dep = _risk_color(dept.pct_geral)
        c.setFillColor(SURFACE); c.setStrokeColor(BORDER); c.setLineWidth(0.5)
        c.roundRect(bx, by - bh, col_w, bh, 3, fill=1, stroke=1)
        c.setFillColor(col_dep)
        c.rect(bx, by - bh, 3, bh, fill=1, stroke=0)

        c.setFillColor(FG); c.setFont("Helvetica-Bold", 8.2)
        c.drawString(bx + 7, by - 6*mm, dept.nome[:20])
        c.setFillColor(col_dep); c.setFont("Helvetica-Bold", 8.6)
        c.drawRightString(bx + col_w - 7, by - 6*mm, f"{dept.pct_geral}%")
        c.setStrokeColor(BORDER); c.setLineWidth(0.5)
        c.line(bx + 4, by - 9*mm, bx + col_w - 4, by - 9*mm)

        iy = by - 11*mm

        if dept.top_criticos:
            c.setFillColor(RED); c.setFont("Helvetica-Bold", 7)
            c.drawString(bx + 6, iy, "Piores:")
            iy -= 5.5*mm
            for eq in dept.top_criticos[:3]:
                pct_eq = int(eq.get("pct", 0))
                c.setFillColor(FG); c.setFont("Helvetica-Bold", 7.5)
                c.drawString(bx + 8, iy, str(eq.get("frota") or "—")[:7])
                c.setFillColor(MUTED); c.setFont("Helvetica", 7)
                c.drawString(bx + 20*mm, iy, str(eq.get("modelo") or "")[:13])
                pbar(bx + col_w - 26*mm, iy - 1.5*mm, 18*mm, 3.5*mm, pct_eq)
                c.setFillColor(_risk_color(pct_eq)); c.setFont("Helvetica-Bold", 7.5)
                c.drawRightString(bx + col_w - 4, iy, f"{pct_eq}%")
                iy -= 6*mm

        if dept.top_melhores:
            c.setFillColor(GREEN); c.setFont("Helvetica-Bold", 7)
            c.drawString(bx + 6, iy, "Quase concluidos:")
            iy -= 5.5*mm
            for eq in dept.top_melhores[:3]:
                pct_eq = int(eq.get("pct", 0))
                c.setFillColor(FG); c.setFont("Helvetica-Bold", 7.5)
                c.drawString(bx + 8, iy, str(eq.get("frota") or "—")[:7])
                c.setFillColor(MUTED); c.setFont("Helvetica", 7)
                c.drawString(bx + 20*mm, iy, str(eq.get("modelo") or "")[:13])
                pbar(bx + col_w - 26*mm, iy - 1.5*mm, 18*mm, 3.5*mm, pct_eq)
                c.setFillColor(GREEN); c.setFont("Helvetica-Bold", 7.5)
                c.drawRightString(bx + col_w - 4, iy, f"{pct_eq}%")
                iy -= 6*mm

        if dept.maiores_evolucoes:
            c.setFillColor(colors.HexColor("#6366F1")); c.setFont("Helvetica-Bold", 7)
            c.drawString(bx + 6, iy, "Evolucoes:")
            iy -= 5.5*mm
            for eq in dept.maiores_evolucoes[:3]:
                pct_eq  = int(eq.get("pct", 0))
                delta_v = pct_eq - int(eq.get("pct_anterior", pct_eq))
                c.setFillColor(FG); c.setFont("Helvetica-Bold", 7.2)
                c.drawString(bx + 8, iy, str(eq.get("frota") or "—")[:7])
                c.setFillColor(MUTED); c.setFont("Helvetica", 6.3)
                c.drawString(bx + 16*mm, iy, str(eq.get("modelo") or "")[:12])

                # mais margem interna para % e delta não vazarem
                bar_x = bx + col_w - 28*mm
                bar_w = 9*mm
                pct_x = bx + col_w - 12*mm
                delta_x = bx + col_w - 7*mm

                pbar(bar_x, iy - 1.4*mm, bar_w, 3.2*mm, pct_eq)
                c.setFillColor(_risk_color(pct_eq)); c.setFont("Helvetica-Bold", 6.6)
                c.drawRightString(pct_x, iy, f"{pct_eq}%")
                if delta_v > 0:
                    c.setFillColor(GREEN); c.setFont("Helvetica-Bold", 5.7)
                    c.drawRightString(delta_x, iy, f"+{delta_v}p")
                iy -= 6*mm

    for dept in deptos:
        bh = dept_block_height(dept)

        # tenta colocar na coluna atual; se não cabe, tenta a outra; se nenhuma cabe, nova página
        placed = False
        for tentativa in range(3):
            if y_col[ci] - bh >= PAGE_BOT:
                draw_dept_block(dept, col_x[ci], y_col[ci], bh)
                y_col[ci] -= bh + 3*mm
                placed = True
                ci = 1 - ci   # alterna coluna
                break
            else:
                # coluna atual não cabe — tenta a outra
                next_ci = 1 - ci
                if y_col[next_ci] - bh >= PAGE_BOT:
                    ci = next_ci
                    # não alterna ainda — vai colocar no próximo loop
                else:
                    # nenhuma coluna cabe — nova página
                    footer(); c.showPage()
                    page_header("Destaques por Departamento (cont.)")
                    y_col = [PAGE_TOP, PAGE_TOP]
                    ci = 0

        if not placed:
            # fallback: força render mesmo que ultrapasse marginalmente
            draw_dept_block(dept, col_x[ci], y_col[ci], bh)
            y_col[ci] -= bh + 3*mm
            ci = 1 - ci

    footer(); c.showPage()

    # ══════════════════════════════════════════════════════════════════════════
    # PÁGINA BÔNUS — Ranking de melhores equipamentos (top 20 cross-deptos)
    # ══════════════════════════════════════════════════════════════════════════
    # Coleta todos os melhores de todos os deptos (quase concluídos + evoluções)
    todos_melhores = []
    todos_evolucoes = []
    for dept in deptos:
        for eq in dept.top_melhores:
            todos_melhores.append({**eq, "_dept": dept.nome})
        for eq in dept.maiores_evolucoes:
            todos_evolucoes.append({**eq, "_dept": dept.nome})

    # Top 20 por % (quase concluídos) e top 20 por delta
    top20_pct   = sorted(todos_melhores,   key=lambda e: -e.get("pct", 0))[:20]
    top20_delta = sorted(todos_evolucoes,  key=lambda e: -(e.get("pct", 0) - int(e.get("pct_anterior", 0))))[:20]

    if top20_pct or top20_delta:
        page_header("Destaques Positivos — Melhores Equipamentos")
        y = h - 20*mm
        row_h2 = 9*mm

        def _eq_row(eq: dict, idx: int, show_delta: bool, y_cur: float) -> float:
            pct_eq  = int(eq.get("pct", 0))
            frota   = str(eq.get("frota") or "—")[:10]
            modelo  = str(eq.get("modelo") or "")[:18]
            dept_n  = str(eq.get("_dept") or "")[:16]
            col_eq  = _risk_color(pct_eq)

            c.setFillColor(SURFACE if idx % 2 == 0 else WHITE)
            c.rect(16*mm, y_cur - row_h2, w - 32*mm, row_h2, fill=1, stroke=0)

            c.setFillColor(FG); c.setFont("Helvetica-Bold", 8)
            c.drawString(18*mm, y_cur - row_h2*0.38, frota)
            c.setFillColor(MUTED); c.setFont("Helvetica", 7)
            c.drawString(18*mm, y_cur - row_h2*0.72, modelo)

            c.setFillColor(MUTED); c.setFont("Helvetica", 7.5)
            c.drawString(50*mm, y_cur - row_h2/2, dept_n)

            bar_x = w/2
            bar_ww = w - 32*mm - (bar_x - 16*mm) - 20*mm
            pbar(bar_x, y_cur - row_h2 + 2*mm, bar_ww, 5*mm, pct_eq)

            c.setFillColor(col_eq); c.setFont("Helvetica-Bold", 8.5)
            c.drawRightString(w - 18*mm, y_cur - row_h2*0.38, f"{pct_eq}%")

            if show_delta:
                delta_v = pct_eq - int(eq.get("pct_anterior", pct_eq))
                if delta_v > 0:
                    c.setFillColor(GREEN); c.setFont("Helvetica-Bold", 7)
                    c.drawRightString(w - 18*mm, y_cur - row_h2*0.72, f"+{delta_v}p.p.")

            c.setStrokeColor(BORDER); c.setLineWidth(0.3)
            c.line(16*mm, y_cur - row_h2, w - 16*mm, y_cur - row_h2)
            return y_cur - row_h2

        # Seção 1 — quase concluídos
        if top20_pct:
            y = section_title("✅ Equipamentos mais próximos de concluir", y)
            # cabeçalho
            c.setFillColor(DARK)
            c.rect(16*mm, y - 6*mm, w - 32*mm, 6*mm, fill=1, stroke=0)
            c.setFillColor(colors.Color(0.6, 0.65, 0.7)); c.setFont("Helvetica-Bold", 7)
            c.drawString(18*mm, y - 4*mm, "Frota / Modelo")
            c.drawString(50*mm, y - 4*mm, "Departamento")
            c.drawString(w/2 + 2*mm, y - 4*mm, "Progresso")
            c.drawRightString(w - 18*mm, y - 4*mm, "%")
            y -= 6*mm
            for i, eq in enumerate(top20_pct):
                if y - row_h2 < 18*mm:
                    footer(); c.showPage()
                    page_header("Destaques Positivos (cont.)")
                    y = h - 20*mm
                y = _eq_row(eq, i, False, y)

        # Seção 2 — maiores evoluções da semana
        if top20_delta:
            y -= 6*mm
            if y < 50*mm:
                footer(); c.showPage()
                page_header("Destaques Positivos — Maiores Evoluções")
                y = h - 20*mm
            y = section_title("📈 Maiores evoluções da semana (cross-departamentos)", y)
            c.setFillColor(DARK)
            c.rect(16*mm, y - 6*mm, w - 32*mm, 6*mm, fill=1, stroke=0)
            c.setFillColor(colors.Color(0.6, 0.65, 0.7)); c.setFont("Helvetica-Bold", 7)
            c.drawString(18*mm, y - 4*mm, "Frota / Modelo")
            c.drawString(50*mm, y - 4*mm, "Departamento")
            c.drawString(w/2 + 2*mm, y - 4*mm, "Progresso atual")
            c.drawRightString(w - 18*mm, y - 4*mm, "% / Δ")
            y -= 6*mm
            for i, eq in enumerate(top20_delta):
                if y - row_h2 < 18*mm:
                    footer(); c.showPage()
                    page_header("Maiores Evoluções (cont.)")
                    y = h - 20*mm
                y = _eq_row(eq, i, True, y)

        footer(); c.showPage()
    c.save()
    return buf.getvalue()
