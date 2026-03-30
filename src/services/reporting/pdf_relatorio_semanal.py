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
from src.utils.timezone import fmt_brt
from typing import List, Tuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle


# ── Paleta ──────────────────────────────────────────────────────────────
FG = colors.Color(0.10, 0.12, 0.16)
MUTED = colors.Color(0.42, 0.46, 0.52)
SURFACE = colors.Color(0.96, 0.97, 0.98)
BORDER = colors.Color(0.88, 0.90, 0.93)
GREEN = colors.HexColor("#12B76A")
YELLOW = colors.HexColor("#F59E0B")
RED = colors.HexColor("#EF4444")
DARK = colors.HexColor("#111827")
WHITE = colors.white


def _risk_color(pct: int) -> colors.Color:
    if pct >= 80:
        return GREEN
    if pct >= 50:
        return YELLOW
    return RED


def _hex(h: str, fallback=colors.gray) -> colors.Color:
    try:
        s = h.lstrip("#")
        r, g, b = int(s[0:2], 16) / 255, int(s[2:4], 16) / \
            255, int(s[4:6], 16) / 255
        return colors.Color(r, g, b)
    except Exception:
        return fallback


# ── Payload de dados ────────────────────────────────────────────────────
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
    # [{frota, modelo, grupo, pct, status}]
    todos_equipamentos: List[dict] = None
    # etapas para cálculo ponderado do pct_global no relatório executivo
    done_steps: int = 0
    expected_steps: int = 0
    # alertas consolidados
    n_travados: int = 0
    n_sem_inicio: int = 0
    n_parados: int = 0
    n_risco_prazo: int = 0
    parados_detalhe: List[dict] = field(default_factory=list)
    # branding
    primary_color: str = "#FFD100"
    logo_url: str | None = None


# ── Gerador ─────────────────────────────────────────────────────────────
def build_weekly_pdf(payload: RelatorioDeptPayload) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    PRIMARY = _hex(payload.primary_color)
    page_no = [0]   # mutável dentro das closures

    # ── helpers comuns ──────────────────────────────────────────────────────
    def new_page(title: str):
        page_no[0] += 1
        # fundo branco
        c.setFillColor(WHITE)
        c.rect(0, 0, w, h, fill=1, stroke=0)
        # faixa superior escura
        c.setFillColor(DARK)
        c.rect(0, h - 26 * mm, w, 26 * mm, fill=1, stroke=0)
        # linha de acento colorida
        c.setFillColor(PRIMARY)
        c.rect(0, h - 26 * mm, w, 2.5, fill=1, stroke=0)
        # nome do tenant + departamento
        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(16 * mm, h - 14 * mm, payload.tenant_nome)
        c.setFont("Helvetica", 9)
        c.setFillColor(colors.Color(1, 1, 1, .6))
        period = f"{payload.revisao_titulo}  ·  Semana {payload.semana_atual}/{payload.semanas_total}"
        c.drawString(16 * mm, h - 20 * mm, period)
        # título da página
        c.setFillColor(FG)
        c.setFont("Helvetica-Bold", 15)
        c.drawString(16 * mm, h - 38 * mm, title)
        # subtítulo departamento
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 10)
        c.drawString(
            16 * mm,
            h - 45 * mm,
            f"Departamento: {payload.departamento_nome}")
        # divider
        c.setStrokeColor(BORDER)
        c.setLineWidth(0.8)
        c.line(16 * mm, h - 48 * mm, w - 16 * mm, h - 48 * mm)

    def footer():
        c.setStrokeColor(BORDER)
        c.setLineWidth(0.8)
        c.line(16 * mm, 14 * mm, w - 16 * mm, 14 * mm)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 8)
        c.drawString(
            16 *
            mm,
            9 *
            mm,
            "Relatório gerado automaticamente — sistema de gestão de revisões.")
        now_str = fmt_brt()
        c.drawRightString(
            w - 16 * mm,
            9 * mm,
            f"{now_str}  ·  pág. {page_no[0]}")

    def section_title(txt: str, y: float) -> float:
        c.setFillColor(FG)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(16 * mm, y, txt)
        c.setStrokeColor(BORDER)
        c.setLineWidth(0.5)
        c.line(16 * mm, y - 2 * mm, w - 16 * mm, y - 2 * mm)
        return y - 8 * mm

    def kpi_row(items: List[Tuple[str, str, colors.Color]],
                top_y: float, card_h: float = 20 * mm) -> float:
        """Renderiza uma linha de KPI cards. items = [(label, valor, cor_valor)]"""
        n = len(items)
        gap = 4 * mm
        card_w = (w - 32 * mm - gap * (n - 1)) / n
        x0 = 16 * mm
        for i, (label, val, val_color) in enumerate(items):
            cx = x0 + i * (card_w + gap)
            cy = top_y - card_h          # y base do card (baixo)
            # card base
            c.setFillColor(SURFACE)
            c.setStrokeColor(BORDER)
            c.setLineWidth(0.7)
            c.roundRect(cx, cy, card_w, card_h, 5, fill=1, stroke=1)
            # barra superior colorida (topo absoluto do card)
            c.setFillColor(PRIMARY)
            c.rect(cx, cy + card_h - 2.5, card_w, 2.5, fill=1, stroke=0)
            # label — posição fixa a 5mm abaixo do topo
            c.setFillColor(MUTED)
            c.setFont("Helvetica", 7.5)
            c.drawString(cx + 5, cy + card_h - 7 * mm, label)
            # valor numérico — posição fixa a 5mm acima da base
            c.setFillColor(val_color)
            c.setFont("Helvetica-Bold", 15)
            c.drawString(cx + 5, cy + 4 * mm, val)
        return top_y - card_h - 5 * mm

    def progress_bar(x: float, y: float, bar_w: float, bar_h: float, pct: int):
        pct_c = max(0, min(100, pct))
        c.setFillColor(BORDER)
        c.roundRect(x, y, bar_w, bar_h, 2, fill=1, stroke=0)
        if pct_c > 0:
            c.setFillColor(_risk_color(pct_c))
            c.roundRect(x, y, bar_w * pct_c / 100, bar_h, 2, fill=1, stroke=0)

    def platypus_table(rows: List[List], col_widths: List[float], top_y: float,
                       header_color=DARK, alt_surface=True) -> float:
        """Desenha uma Table do platypus no canvas. Retorna y após a tabela."""
        tbl = Table(rows, colWidths=col_widths)
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), header_color),
            ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8.5),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("FONTSIZE", (0, 1), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.3, BORDER),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ALIGN", (1, 1), (-1, -1), "CENTER"),
        ]
        if alt_surface:
            for r in range(1, len(rows)):
                if r % 2 == 0:
                    style.append(("BACKGROUND", (0, r), (-1, r), SURFACE))
        tbl.setStyle(TableStyle(style))
        tw, th = tbl.wrapOn(c, w - 32 * mm, h)
        draw_y = top_y - th
        tbl.drawOn(c, 16 * mm, draw_y)
        return draw_y - 6 * mm

    def draw_alert_summary_page():
        new_page("Resumo de Alertas")
        y = h - 52 * mm

        alert_items = [
            ("Travados",
             payload.n_travados,
             RED,
             "Status travado sem atualização recente."),
            ("Sem apontamento",
             payload.n_sem_inicio,
             YELLOW,
             "Etapas D/R/M nunca iniciadas."),
            ("Parados",
             payload.n_parados,
             YELLOW,
             "Sem manutenção / movimentação recente."),
            ("Risco de prazo",
             payload.n_risco_prazo,
             RED,
             "Progresso abaixo da meta linear da semana."),
        ]
        card_w_a = (w - 36 * mm) / 2
        card_h_a = 24 * mm
        gap_a = 4 * mm
        for idx, (title_a, count, col, desc) in enumerate(alert_items):
            col_i = idx % 2
            row_i = idx // 2
            cx = 16 * mm + col_i * (card_w_a + gap_a)
            cy = y - row_i * (card_h_a + gap_a) - card_h_a
            c.setFillColor(SURFACE)
            c.setStrokeColor(BORDER)
            c.setLineWidth(0.7)
            c.roundRect(cx, cy, card_w_a, card_h_a, 5, fill=1, stroke=1)
            c.setFillColor(col)
            c.rect(cx, cy + card_h_a - 3, card_w_a, 3, fill=1, stroke=0)
            c.setFillColor(col)
            c.setFont("Helvetica-Bold", 26)
            c.drawString(cx + 6, cy + 6 * mm, str(count))
            c.setFillColor(FG)
            c.setFont("Helvetica-Bold", 9)
            c.drawString(cx + 6, cy + card_h_a - 8 * mm, title_a)
            c.setFillColor(MUTED)
            c.setFont("Helvetica", 7.5)
            c.drawString(cx + 6, cy + 3 * mm, desc)
            if count == 0:
                c.setFillColor(GREEN)
                c.setFont("Helvetica-Bold", 8)
                c.drawRightString(cx + card_w_a - 5, cy + 6 * mm, "OK")

        y -= 2 * (card_h_a + gap_a) + 6 * mm
        if payload.parados_detalhe:
            y = section_title("Equipamentos sem movimentação", y)
            rows = [["Frota", "Grupo", "Últ. semana", "Dias", "Progresso"]]
            for item in payload.parados_detalhe[:12]:
                rows.append([
                    str(item.get("frota") or "—"),
                    str(item.get("grupo") or "—")[:24],
                    f"Sem. {item.get('ultima_semana')}" if item.get("ultima_semana") else "Sem registro",
                    str(item.get("dias_parado") or "—"),
                    f"{int(item.get('progresso') or 0)}%",
                ])
            pw_useful = w - 32 * mm
            y = platypus_table(rows,
                               [pw_useful * 0.14,
                                pw_useful * 0.36,
                                pw_useful * 0.18,
                                pw_useful * 0.12,
                                pw_useful * 0.20],
                               y)
            c.setFillColor(MUTED)
            c.setFont("Helvetica", 8)
            c.drawString(
                16 *
                mm,
                max(
                    y,
                    28 *
                    mm),
                "Alerta baseado na última manutenção/movimentação registrada por equipamento.")
            y -= 8 * mm
        y = max(y, 46 * mm)
        c.setFillColor(FG)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(
            16 *
            mm,
            y,
            "Legenda de status — faixa lateral nas listas de equipamentos")
        y -= 7 * mm
        for s_col, s_label, s_desc in [
            (RED, "Crítico", "0% ou travado — requer ação imediata"),
            (YELLOW, "Em andamento", "Iniciado mas não concluído"),
            (GREEN, "Concluído", "100% das etapas D+R+M finalizadas"),
            (MUTED, "Sem template", "Grupo sem serviços configurados"),
        ]:
            c.setFillColor(s_col)
            c.roundRect(16 * mm, y - 4 * mm, 8, 4 * mm, 2, fill=1, stroke=0)
            c.setFillColor(FG)
            c.setFont("Helvetica-Bold", 8)
            c.drawString(20 * mm, y - 1 * mm, s_label)
            c.setFillColor(MUTED)
            c.setFont("Helvetica", 8)
            c.drawString(40 * mm, y - 1 * mm, s_desc)
            y -= 7 * mm
        footer()
        c.showPage()

    # ── PÁGINA 1: Capa / KPIs ───────────────────────────────────────────────
    new_page("Relatório Semanal")
    y = h - 52 * mm

    # KPIs linha 1
    pct_color = _risk_color(payload.pct_geral)
    y = kpi_row([
        ("Progresso geral", f"{payload.pct_geral}%", pct_color),
        ("Equipamentos", str(payload.n_equipamentos), FG),
        ("Concluídos (100%)", str(payload.n_concluidos), GREEN),
        ("Alertas ativos", str(payload.n_alertas_total),
         RED if payload.n_alertas_total else GREEN),
    ], top_y=y)

    # Barra de progresso geral
    y -= 2 * mm
    bar_w = w - 32 * mm
    progress_bar(16 * mm, y, bar_w, 7 * mm, payload.pct_geral)
    c.setFillColor(_risk_color(payload.pct_geral))
    c.setFont("Helvetica-Bold", 8.5)
    c.drawRightString(w - 16 * mm, y + 8 * mm, f"{payload.pct_geral}%")
    y -= 12 * mm

    # KPIs alertas
    y = kpi_row([
        ("Travados", str(payload.n_travados), RED if payload.n_travados else MUTED),
        ("Sem inicio", str(payload.n_sem_inicio), YELLOW if payload.n_sem_inicio else MUTED),
        ("Parados", str(payload.n_parados), YELLOW if payload.n_parados else MUTED),
        ("Risco prazo", str(payload.n_risco_prazo), RED if payload.n_risco_prazo else MUTED),
    ], top_y=y, card_h=20 * mm)

    # Comparativo S-1 vs S atual — inline na capa
    y -= 2 * mm
    y = section_title("Comparativo — semana anterior vs. atual", y)
    delta = payload.pct_semana_atual - payload.pct_semana_anterior
    delta_str = f"+{delta}p.p." if delta >= 0 else f"{delta}p.p."
    delta_color = GREEN if delta >= 0 else RED

    box_w = (w - 32 * mm - 8 * mm) / 3
    bx = 16 * mm
    for label, val, col in [
        ("Semana anterior", f"{payload.pct_semana_anterior}%", _risk_color(payload.pct_semana_anterior)),
        ("Semana atual", f"{payload.pct_semana_atual}%", _risk_color(payload.pct_semana_atual)),
        ("Variação", delta_str, delta_color),
    ]:
        c.setFillColor(SURFACE)
        c.setStrokeColor(BORDER)
        c.setLineWidth(0.7)
        c.roundRect(bx, y - 14 * mm, box_w, 14 * mm, 4, fill=1, stroke=1)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 8)
        c.drawString(bx + 6, y - 5 * mm, label)
        c.setFillColor(col)
        c.setFont("Helvetica-Bold", 15)
        c.drawString(bx + 6, y - 13 * mm, val)
        bx += box_w + 4 * mm
    y -= 20 * mm

    # Top 3 críticos inline na capa se houver espaço
    piores_capa = sorted(
        [e for e in (payload.todos_equipamentos or []) if e.get("pct", 0) < 50],
        key=lambda e: e.get("pct", 0)
    )[:3]
    if piores_capa and y > 60 * mm:
        y -= 2 * mm
        y = section_title("🔴 Equipamentos críticos em destaque", y)
        row_hc = 7 * mm
        for i, eq in enumerate(piores_capa):
            if y - row_hc < 18 * mm:
                break
            pct = int(eq.get("pct", 0))
            frota = str(eq.get("frota") or "—")[:10]
            modelo = str(eq.get("modelo") or "")[:20]
            grupo = str(eq.get("grupo") or "")[:14]
            col = _risk_color(pct)
            c.setFillColor(SURFACE if i % 2 == 0 else WHITE)
            c.rect(16 * mm, y - row_hc, w - 32 * mm, row_hc, fill=1, stroke=0)
            c.setFillColor(FG)
            c.setFont("Helvetica-Bold", 8)
            c.drawString(18 * mm, y - row_hc * 0.38, frota)
            c.setFillColor(MUTED)
            c.setFont("Helvetica", 7.5)
            c.drawString(32 * mm, y - row_hc * 0.38, modelo)
            c.drawString(80 * mm, y - row_hc * 0.38, grupo)
            progress_bar(
                w / 2 + 10 * mm,
                y - row_hc + 1.5 * mm,
                35 * mm,
                row_hc - 3 * mm,
                pct)
            c.setFillColor(col)
            c.setFont("Helvetica-Bold", 8.5)
            c.drawRightString(w - 17 * mm, y - row_hc * 0.38, f"{pct}%")
            c.setStrokeColor(BORDER)
            c.setLineWidth(0.3)
            c.line(16 * mm, y - row_hc, w - 16 * mm, y - row_hc)
            y -= row_hc

    footer()
    c.showPage()

    # ── PÁGINA 2: Evolução semanal + top equipamentos ───────────────────────
    new_page("Evolução Semanal")
    y = h - 52 * mm

    # Layout 2 colunas: esquerda = gráfico evolução, direita = top
    # piores/melhores
    left_w = (w - 36 * mm) * 0.55
    right_w = (w - 36 * mm) * 0.42
    left_x = 16 * mm
    right_x = left_x + left_w + 4 * mm
    y_left = y
    y_right = y

    # Coluna esquerda: evolução semanal em barras horizontais
    c.setFillColor(FG)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(left_x, y_left, "Progresso acumulado por semana")
    y_left -= 7 * mm

    if payload.evolucao:
        sem_atual = payload.semana_atual
        bar_label_w = 16 * mm
        bar_pct_w = 10 * mm
        bar_avail = left_w - bar_label_w - bar_pct_w - 4 * mm
        row_h = 6 * mm

        for snap in payload.evolucao:
            if y_left - row_h < 24 * mm:
                break
            is_current = snap.semana == sem_atual
            if is_current:
                c.setFillColor(PRIMARY)
                c.setFont("Helvetica-Bold", 8)
            else:
                c.setFillColor(MUTED)
                c.setFont("Helvetica", 8)
            c.drawString(left_x, y_left - row_h / 2, f"Sem.{snap.semana}")
            bx = left_x + bar_label_w
            progress_bar(
                bx,
                y_left - row_h + 1 * mm,
                bar_avail,
                row_h - 2 * mm,
                snap.pct)
            c.setFillColor(_risk_color(snap.pct))
            c.setFont("Helvetica-Bold", 8)
            c.drawRightString(left_x + left_w, y_left -
                              row_h / 2, f"{snap.pct}%")
            y_left -= row_h + 1 * mm
    else:
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 9)
        c.drawString(left_x, y_left - 8 * mm, "Sem dados de evolução.")
        y_left -= 14 * mm

    # Coluna direita: top piores + top melhores
    todos = payload.todos_equipamentos or []
    c.setFillColor(FG)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(right_x, y_right, "Destaques de equipamentos")
    y_right -= 7 * mm

    def _right_eq_row(eq: dict, col_label_color, y_r: float) -> float:
        if y_r - 6 * mm < 24 * mm:
            return y_r
        pct_eq = int(eq.get("pct", 0))
        frota = str(eq.get("frota") or "—")[:8]
        modelo = str(eq.get("modelo") or "")[:12]
        col = _risk_color(pct_eq)
        c.setFillColor(FG)
        c.setFont("Helvetica-Bold", 7.5)
        c.drawString(right_x + 2, y_r - 3 * mm, frota)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 7)
        c.drawString(right_x + 16 * mm, y_r - 3 * mm, modelo)
        pb_x = right_x + right_w - 22 * mm
        progress_bar(pb_x, y_r - 5 * mm, 14 * mm, 3.5 * mm, pct_eq)
        c.setFillColor(col)
        c.setFont("Helvetica-Bold", 7.5)
        c.drawRightString(right_x + right_w, y_r - 3 * mm, f"{pct_eq}%")
        c.setStrokeColor(BORDER)
        c.setLineWidth(0.3)
        c.line(right_x, y_r - 6 * mm, right_x + right_w, y_r - 6 * mm)
        return y_r - 6 * mm

    piores_pg2 = sorted([e for e in todos if e.get(
        "pct", 0) < 100], key=lambda e: e.get("pct", 0))[:5]
    if piores_pg2:
        c.setFillColor(RED)
        c.setFont("Helvetica-Bold", 7.5)
        c.drawString(right_x, y_right, "⚠ Piores:")
        y_right -= 5 * mm
        for eq in piores_pg2:
            y_right = _right_eq_row(eq, RED, y_right)

    melhores_pg2 = sorted([e for e in todos if e.get(
        "pct", 0) < 100], key=lambda e: -e.get("pct", 0))[:5]
    if melhores_pg2:
        y_right -= 3 * mm
        c.setFillColor(GREEN)
        c.setFont("Helvetica-Bold", 7.5)
        c.drawString(right_x, y_right, "✅ Mais avançados:")
        y_right -= 5 * mm
        for eq in melhores_pg2:
            y_right = _right_eq_row(eq, GREEN, y_right)

    footer()
    c.showPage()

    # ── PÁGINA 3: Resumo de alertas ─────────────────────────────────────────
    draw_alert_summary_page()

    # ── PÁGINA 4: Equipamentos críticos ─────────────────────────────────────
    new_page("Equipamentos Críticos")
    y = h - 52 * mm

    status_labels = {
        "zero": "0% — Sem início",
        "travado": "Travado",
        "atrasado": "Atrasado"}
    status_colors = {"zero": RED, "travado": RED, "atrasado": YELLOW}

    if payload.criticos:
        total_criticos = len(payload.criticos)
        MAX_POR_PAGINA = 35  # linhas por página (tamanho A4 comporta ~35 linhas de 7pt)
        MAX_TOTAL = 150      # limite absoluto para não gerar PDFs de centenas de páginas

        criticos_exibir = payload.criticos[:MAX_TOTAL]
        truncado = total_criticos > MAX_TOTAL

        y = section_title(
            f"{total_criticos} equipamento(s) requerem atenção"
            + (f" — exibindo os {MAX_TOTAL} mais críticos" if truncado else ""),
            y)

        # Divide em páginas de MAX_POR_PAGINA linhas
        for page_start in range(0, len(criticos_exibir), MAX_POR_PAGINA):
            chunk = criticos_exibir[page_start:page_start + MAX_POR_PAGINA]
            rows = [["Frota", "Modelo", "Grupo", "Progresso", "Situação"]]
            for eq in chunk:
                rows.append([
                    eq.frota,
                    eq.modelo[:20] if eq.modelo else "—",
                    eq.grupo[:18] if eq.grupo else "—",
                    f"{eq.pct}%",
                    status_labels.get(eq.status, eq.status),
                ])
            pw_useful = w - 32 * mm
            y = platypus_table(rows,
                               [pw_useful * 0.12,
                                pw_useful * 0.26,
                                pw_useful * 0.24,
                                pw_useful * 0.14,
                                pw_useful * 0.24],
                               y)

            # Se há mais páginas, quebra e abre nova
            if page_start + MAX_POR_PAGINA < len(criticos_exibir):
                footer()
                c.showPage()
                new_page("Equipamentos Críticos (cont.)")
                y = h - 52 * mm

        if truncado:
            c.setFillColor(MUTED)
            c.setFont("Helvetica-Oblique", 7.5)
            c.drawString(16 * mm, y,
                f"… e mais {total_criticos - MAX_TOTAL} equipamentos não exibidos.")
            y -= 6 * mm
    else:
        c.setFillColor(GREEN)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(
            16 * mm,
            y - 10 * mm,
            "✓ Nenhum equipamento crítico neste departamento.")

    footer()
    c.showPage()

    # ── PÁGINA 3b: Resumo Executivo de Equipamentos ─────────────────────────
    todos = payload.todos_equipamentos or []
    if todos:
        new_page("Resumo de Equipamentos")
        y = h - 52 * mm

        # ── helpers locais ───────────────────────────────────────────────────
        def _mini_eq_row(eq: dict, idx: int, show_delta: bool = False):
            nonlocal y
            if y < 28 * mm:
                return False  # sinaliza que não coube
            pct = int(eq.get("pct", 0))
            frota = str(eq.get("frota") or "—")[:10]
            modelo = str(eq.get("modelo") or "")[:16]
            grupo = str(eq.get("grupo") or "")[:14]
            col = _risk_color(pct)
            row_h_ = 7.5 * mm

            bg = SURFACE if idx % 2 == 0 else WHITE
            c.setFillColor(bg)
            c.rect(16 * mm, y - row_h_, w - 32 * mm, row_h_, fill=1, stroke=0)

            c.setFillColor(FG)
            c.setFont("Helvetica-Bold", 8)
            c.drawString(17 * mm, y - row_h_ * 0.35, frota)
            c.setFillColor(MUTED)
            c.setFont("Helvetica", 7)
            c.drawString(17 * mm, y - row_h_ * 0.72, modelo)

            c.setFillColor(MUTED)
            c.setFont("Helvetica", 7.5)
            c.drawString(42 * mm, y - row_h_ / 2, grupo)

            bar_x = 78 * mm
            bar_w_ = w - 32 * mm - 62 * mm - 16 * mm
            progress_bar(
                bar_x,
                y - row_h_ + 1.5 * mm,
                bar_w_,
                row_h_ - 3 * mm,
                pct)

            c.setFillColor(col)
            c.setFont("Helvetica-Bold", 8.5)
            c.drawRightString(w - 17 * mm, y - row_h_ * 0.38, f"{pct}%")

            if show_delta:
                delta = pct - int(eq.get("pct_anterior", pct))
                if delta != 0:
                    d_col = GREEN if delta > 0 else RED
                    d_str = f"+{delta}p.p." if delta > 0 else f"{delta}p.p."
                    c.setFillColor(d_col)
                    c.setFont("Helvetica-Bold", 7)
                    c.drawRightString(w - 17 * mm, y - row_h_ * 0.72, d_str)

            c.setStrokeColor(BORDER)
            c.setLineWidth(0.3)
            c.line(16 * mm, y - row_h_, w - 16 * mm, y - row_h_)
            y -= row_h_
            return True

        # ── 1. Totais por faixa ──────────────────────────────────────────────
        n_verde = sum(1 for e in todos if e.get("pct", 0) >= 80)
        n_amarelo = sum(1 for e in todos if 50 <= e.get("pct", 0) < 80)
        n_vermelho = sum(1 for e in todos if e.get("pct", 0) < 50)
        total_eq = len(todos)

        y = section_title("Distribuição por faixa de progresso", y)
        gap = 4 * mm
        bw = (w - 32 * mm - gap * 2) / 3
        bh = 18 * mm
        x0 = 16 * mm
        for label, count, col, sub in [
            ("✅  ≥ 80% — Em dia", n_verde, GREEN, f"{round(n_verde / max(total_eq, 1) * 100)}% dos equipamentos"),
            ("⚠️  50–79% — Atenção", n_amarelo, YELLOW, f"{round(n_amarelo / max(total_eq, 1) * 100)}% dos equipamentos"),
            ("🔴  < 50% — Crítico", n_vermelho, RED, f"{round(n_vermelho / max(total_eq, 1) * 100)}% dos equipamentos"),
        ]:
            cx = x0
            c.setFillColor(SURFACE)
            c.setStrokeColor(BORDER)
            c.setLineWidth(0.7)
            c.roundRect(cx, y - bh, bw, bh, 4, fill=1, stroke=1)
            c.setFillColor(col)
            c.rect(cx, y - 2.5, bw, 2.5, fill=1, stroke=0)
            c.setFillColor(col)
            c.setFont("Helvetica-Bold", 20)
            c.drawString(cx + 5, y - bh + 5 * mm, str(count))
            c.setFillColor(MUTED)
            c.setFont("Helvetica", 7)
            c.drawString(cx + 5, y - bh + 2 * mm, sub)
            c.setFillColor(FG)
            c.setFont("Helvetica-Bold", 7.5)
            c.drawString(cx + 5, y - 7 * mm, label)
            x0 += bw + gap

        y -= bh + 8 * mm

        # ── 2. Top 5 piores ──────────────────────────────────────────────────
        piores = sorted([e for e in todos if e.get("status") !=
                        "sem_template"], key=lambda e: e.get("pct", 0))[:5]
        if piores:
            y -= 2 * mm
            y = section_title("🔴 5 equipamentos com menor progresso", y)
            for i, eq in enumerate(piores):
                if not _mini_eq_row(eq, i):
                    break

        # ── 3. Top 5 melhores (quase concluídos) ─────────────────────────────
        melhores = sorted([e for e in todos if e.get("pct", 0) < 100 and e.get(
            "status") != "sem_template"], key=lambda e: -e.get("pct", 0))[:5]
        if melhores:
            y -= 4 * mm
            if y < 60 * mm:
                footer()
                c.showPage()
                new_page("Resumo de Equipamentos (cont.)")
                y = h - 52 * mm
            y = section_title("✅ 5 equipamentos mais próximos de concluir", y)
            for i, eq in enumerate(melhores):
                if not _mini_eq_row(eq, i):
                    break

        # ── 4. Top 5 maiores evoluções ───────────────────────────────────────
        com_delta = [e for e in todos if e.get("pct_anterior") is not None
                     and e.get("pct", 0) - int(e.get("pct_anterior", 0)) > 0]
        maiores_evolucao = sorted(
            com_delta, key=lambda e: -(e.get("pct", 0) - int(e.get("pct_anterior", 0))))[:5]
        if maiores_evolucao:
            y -= 4 * mm
            if y < 60 * mm:
                footer()
                c.showPage()
                new_page("Resumo de Equipamentos (cont.)")
                y = h - 52 * mm
            y = section_title(
                "📈 5 equipamentos com maior evolução na semana", y)
            for i, eq in enumerate(maiores_evolucao):
                if not _mini_eq_row(eq, i, show_delta=True):
                    break

        # ── PÁGINA 3+: Uma página por grupo ──────────────────────────────────
        from collections import defaultdict as _defaultdict

        # Agrupa equipamentos por (grupo_id, grupo_nome, grupo_pct)
        grupos_dict: dict[str, list[dict]] = _defaultdict(list)
        grupos_meta: dict[str, dict] = {}   # gid → {nome, pct}
        for eq in todos:
            gid = eq.get("grupo_id") or "__sem_grupo__"
            gname = eq.get("grupo") or "Sem grupo"
            grupos_dict[gid].append(eq)
            grupos_meta[gid] = {
                "nome": gname,
                "pct": int(eq.get("grupo_pct", 0)),
            }

        # Ordena grupos: piores primeiro
        grupos_sorted = sorted(
            grupos_meta.keys(),
            key=lambda g: grupos_meta[g]["pct"])

        for gi, gid in enumerate(grupos_sorted):
            eqs_grupo = sorted(grupos_dict[gid], key=lambda e: -e.get("pct", 0))
            gmeta = grupos_meta[gid]
            gname = gmeta["nome"]

            # Recalcula o percentual do header a partir dos próprios
            # equipamentos exibidos na página do grupo.
            done_sum = sum(int(e.get("done_steps", 0) or 0) for e in eqs_grupo)
            total_sum = sum(int(e.get("total_steps", 0) or 0) for e in eqs_grupo)
            if total_sum > 0:
                gpct = int(round((done_sum / total_sum) * 100))
            else:
                pcts = [float(e.get("pct", 0) or 0) for e in eqs_grupo]
                gpct = int(round(sum(pcts) / len(pcts))) if pcts else int(gmeta.get("pct", 0) or 0)

            gcol = _risk_color(gpct)
            n_eq_g = len(eqs_grupo)
            n_conc_g = sum(1 for e in eqs_grupo if e.get("pct", 0) == 100)
            n_crit_g = sum(
                1 for e in eqs_grupo if e.get(
                    "pct", 0) == 0 and e.get("status") != "sem_template")
            n_trav_g = sum(
                1 for e in eqs_grupo if e.get("status") == "travado")

            # ── Cabeçalho da página do grupo ─────────────────────────────────
            footer()
            c.showPage()

            # Fundo escuro no topo (maior que o new_page padrão)
            c.setFillColor(DARK)
            c.rect(0, h - 38 * mm, w, 38 * mm, fill=1, stroke=0)
            c.setFillColor(gcol)
            c.rect(0, h - 38 * mm, w, 2.5, fill=1,
                   stroke=0)   # linha de acento

            # Breadcrumb
            c.setFillColor(colors.Color(0.5, 0.55, 0.62))
            c.setFont("Helvetica", 8)
            c.drawString(
                16 * mm, h - 10 * mm, f"{payload.tenant_nome}  ›  {payload.departamento_nome}  ›  {payload.revisao_titulo}  ·  Semana {payload.semana_atual}/{payload.semanas_total}")

            # Nome do grupo grande
            c.setFillColor(WHITE)
            c.setFont("Helvetica-Bold", 17)
            c.drawString(16 * mm, h - 20 * mm, gname)

            # Barra de progresso do grupo
            bar_w_g = w - 32 * mm
            c.setFillColor(colors.Color(0.18, 0.21, 0.27))
            c.roundRect(
                16 * mm,
                h - 28 * mm,
                bar_w_g,
                5 * mm,
                2.5,
                fill=1,
                stroke=0)
            fw_g = max(bar_w_g * gpct / 100, 5 * mm if gpct > 0 else 0)
            c.setFillColor(gcol)
            c.roundRect(
                16 * mm,
                h - 28 * mm,
                fw_g,
                5 * mm,
                2.5,
                fill=1,
                stroke=0)
            c.setFillColor(gcol)
            c.setFont("Helvetica-Bold", 13)
            c.drawRightString(w - 16 * mm, h - 23 * mm, f"{gpct}%")

            # KPI strip do grupo (4 cards compactos)
            y_kpi = h - 38 * mm - 1 * mm
            kpi_h = 14 * mm
            kpi_ww = (w - 36 * mm) / 4
            for ki, (klbl, kval, kcol) in enumerate([
                ("Equipamentos", str(n_eq_g), FG),
                ("Concluídos", str(n_conc_g), GREEN),
                ("Sem início", str(n_crit_g), RED if n_crit_g else MUTED),
                ("Travados", str(n_trav_g), RED if n_trav_g else MUTED),
            ]):
                kx = 16 * mm + ki * (kpi_ww + 1.3 * mm)
                c.setFillColor(SURFACE)
                c.setStrokeColor(BORDER)
                c.setLineWidth(0.5)
                c.roundRect(
                    kx,
                    y_kpi -
                    kpi_h,
                    kpi_ww,
                    kpi_h,
                    3,
                    fill=1,
                    stroke=1)
                c.setFillColor(kcol)
                c.setFont("Helvetica-Bold", 14)
                c.drawString(kx + 4, y_kpi - 9 * mm, kval)
                c.setFillColor(MUTED)
                c.setFont("Helvetica", 7)
                c.drawString(kx + 4, y_kpi - 13 * mm, klbl)

            y = y_kpi - kpi_h - 4 * mm

            # ── Lista de equipamentos ────────────────────────────────────────
            # Cabeçalho da tabela
            c.setFillColor(DARK)
            c.rect(16 * mm, y - 6 * mm, w - 32 * mm, 6 * mm, fill=1, stroke=0)
            c.setFillColor(colors.Color(0.6, 0.65, 0.7))
            c.setFont("Helvetica-Bold", 7.5)
            c.drawString(22 * mm, y - 4 * mm, "Frota")
            c.drawString(36 * mm, y - 4 * mm, "Modelo")
            c.drawString(w / 2, y - 4 * mm, "Progresso")
            c.drawRightString(w - 18 * mm, y - 4 * mm, "%  /  Δ semana")
            y -= 6 * mm

            row_h = 8 * mm
            for i, eq in enumerate(eqs_grupo):
                if y - row_h < 18 * mm:
                    footer()
                    c.showPage()
                    # cabeçalho de continuação compacto
                    c.setFillColor(DARK)
                    c.rect(0, h - 14 * mm, w, 14 * mm, fill=1, stroke=0)
                    c.setFillColor(gcol)
                    c.rect(0, h - 14 * mm, 4, 14 * mm, fill=1, stroke=0)
                    c.setFillColor(WHITE)
                    c.setFont("Helvetica-Bold", 10)
                    c.drawString(10 * mm, h - 9 * mm, gname)
                    c.setFillColor(colors.Color(0.5, 0.55, 0.62))
                    c.setFont("Helvetica", 8)
                    c.drawRightString(
                        w - 10 * mm, h - 9 * mm, f"{gpct}%  (cont.)")
                    y = h - 18 * mm
                    # re-cabeçalho da tabela
                    c.setFillColor(colors.Color(0.92, 0.93, 0.95))
                    c.rect(
                        16 * mm,
                        y - 6 * mm,
                        w - 32 * mm,
                        6 * mm,
                        fill=1,
                        stroke=0)
                    c.setFillColor(MUTED)
                    c.setFont("Helvetica-Bold", 7.5)
                    c.drawString(22 * mm, y - 4 * mm, "Frota")
                    c.drawString(36 * mm, y - 4 * mm, "Modelo")
                    c.drawString(w / 2, y - 4 * mm, "Progresso")
                    c.drawRightString(w - 18 * mm, y - 4 * mm, "%  /  Δ")
                    y -= 6 * mm

                pct = int(eq.get("pct", 0))
                frota = str(eq.get("frota") or "—")[:8]
                modelo = str(eq.get("modelo") or "")[:24]
                col = _risk_color(pct)
                status = eq.get("status", "")

                # fundo alternado
                c.setFillColor(SURFACE if i % 2 == 0 else WHITE)
                c.rect(
                    16 * mm,
                    y - row_h,
                    w - 32 * mm,
                    row_h,
                    fill=1,
                    stroke=0)

                # faixa lateral de status
                s_col = RED if status in ("zero", "travado") else (
                    YELLOW if status == "em_andamento" else GREEN)
                c.setFillColor(s_col)
                c.rect(16 * mm, y - row_h, 3, row_h, fill=1, stroke=0)

                # frota + modelo
                c.setFillColor(FG)
                c.setFont("Helvetica-Bold", 8.5)
                c.drawString(22 * mm, y - row_h * 0.38, frota)
                c.setFillColor(MUTED)
                c.setFont("Helvetica", 7.5)
                c.drawString(36 * mm, y - row_h * 0.38, modelo)

                # barra de progresso
                bar_x = w / 2
                bar_ww = w - 32 * mm - (bar_x - 16 * mm) - 18 * mm
                progress_bar(
                    bar_x,
                    y - row_h + 2 * mm,
                    bar_ww,
                    row_h - 4 * mm,
                    pct)

                # %
                c.setFillColor(col)
                c.setFont("Helvetica-Bold", 9)
                c.drawRightString(w - 18 * mm, y - row_h * 0.38, f"{pct}%")

                # delta
                delta_v = pct - int(eq.get("pct_anterior", pct))
                if delta_v != 0:
                    d_col = GREEN if delta_v > 0 else RED
                    d_str = f"+{delta_v}p.p." if delta_v > 0 else f"{delta_v}p.p."
                    c.setFillColor(d_col)
                    c.setFont("Helvetica-Bold", 7)
                    c.drawRightString(w - 18 * mm, y - row_h * 0.72, d_str)

                c.setStrokeColor(BORDER)
                c.setLineWidth(0.3)
                c.line(16 * mm, y - row_h, w - 16 * mm, y - row_h)
                y -= row_h

    c.save()
    return buf.getvalue()
