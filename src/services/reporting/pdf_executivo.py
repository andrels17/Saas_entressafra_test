
from __future__ import annotations

import io
import math
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle
from reportlab.lib.utils import ImageReader


@dataclass
class Branding:
    company_name: str = "AgroSafra"
    logo_url: str | None = None
    primary_color: str = "#FFD100"   # vermelho
    accent_color: str = "#7F1D1D"    # vermelho escuro
    footer_note: str = "Relatório gerado automaticamente."


def _hex_to_color(hex_str: str, fallback: colors.Color = colors.red) -> colors.Color:
    try:
        s = (hex_str or "").strip()
        if not s:
            return fallback
        if s.startswith("#"):
            s = s[1:]
        if len(s) == 3:
            s = "".join([c*2 for c in s])
        r = int(s[0:2], 16) / 255.0
        g = int(s[2:4], 16) / 255.0
        b = int(s[4:6], 16) / 255.0
        return colors.Color(r, g, b)
    except Exception:
        return fallback


def _safe_get(d: Dict[str, Any], *keys, default=None):
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _download_logo(logo_url: str) -> Optional[ImageReader]:
    try:
        with urllib.request.urlopen(logo_url, timeout=6) as resp:
            data = resp.read()
        return ImageReader(io.BytesIO(data))
    except Exception:
        return None


def _fmt_dt(dt_str: str | None) -> str:
    if not dt_str:
        return "-"
    try:
        # aceita ISO 8601
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(dt_str)


def build_executive_pdf(
    summary: Dict[str, Any],
    branding: Branding,
    period_label: str,
    tenant_label: str,
) -> bytes:
    """
    Gera um PDF executivo white-label (logo + cores) em bytes.
    summary: retorno do RPC get_executive_summary (dict)
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4

    primary = _hex_to_color(branding.primary_color, colors.red)
    accent = _hex_to_color(branding.accent_color, colors.darkred)

    # Tema claro (fundo branco) – melhor para impressão/compartilhamento
    bg = colors.white
    fg = colors.Color(0.10, 0.12, 0.16)          # quase preto
    muted = colors.Color(0.42, 0.46, 0.52)
    surface = colors.Color(0.96, 0.97, 0.98)     # cards/tabelas
    border = colors.Color(0.88, 0.90, 0.93)

    # --- helper: header ---
    def header(page_title: str):
        # page background
        c.setFillColor(bg)
        c.rect(0, 0, w, h, fill=1, stroke=0)

        # top band + accent line
        c.setFillColor(bg)
        c.rect(0, h-28*mm, w, 28*mm, fill=1, stroke=0)
        c.setFillColor(primary)
        c.rect(0, h-28*mm, w, 3.2, fill=1, stroke=0)

        # logo (se houver)
        x0 = 16*mm
        y0 = h-22*mm
        if branding.logo_url:
            img = _download_logo(branding.logo_url)
            if img:
                # encaixar em 18mm altura
                c.drawImage(img, x0, y0-10*mm, width=22*mm, height=18*mm, mask='auto')
                x0 += 26*mm

        c.setFillColor(fg)
        c.setFont("Helvetica-Bold", 15)
        c.drawString(x0, h-16*mm, branding.company_name)

        c.setFillColor(muted)
        c.setFont("Helvetica", 9)
        c.drawString(x0, h-22*mm, f"{tenant_label}  •  {period_label}")

        # título da página
        c.setFillColor(fg)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(16*mm, h-40*mm, page_title)

        # divider
        c.setStrokeColor(border)
        c.setLineWidth(1)
        c.line(16*mm, h-42*mm, w-16*mm, h-42*mm)

    def footer(page_no: int):
        c.setStrokeColor(border)
        c.setLineWidth(1)
        c.line(16*mm, 16*mm, w-16*mm, 16*mm)

        c.setFillColor(muted)
        c.setFont("Helvetica", 8)
        c.drawString(16*mm, 11*mm, branding.footer_note)
        c.drawRightString(w-16*mm, 11*mm, f"{datetime.now().strftime('%d/%m/%Y %H:%M')}  •  pág. {page_no}")

    def kpi_cards(kpis: Dict[str, Any], top_y: float):
        # 4 cards 2x2
        cards = [
            ("Total", int(kpis.get("total_tarefas", 0))),
            ("Concluídas", int(kpis.get("concluidas", 0))),
            ("Travadas", int(kpis.get("travadas", 0))),
            ("SLA médio (dias)", float(kpis.get("sla_dias", 0.0))),
        ]
        card_w = (w - 32*mm - 10*mm) / 2
        card_h = 18*mm
        gap = 10*mm
        x = 16*mm
        y = top_y
        for i,(label,val) in enumerate(cards):
            cx = x + (i%2)*(card_w+gap)
            cy = y - (i//2)*(card_h+gap)
            # card surface
            c.setFillColor(surface)
            c.setStrokeColor(border)
            c.setLineWidth(1)
            c.roundRect(cx, cy-card_h, card_w, card_h, 7, fill=1, stroke=1)
            # accent top line
            c.setFillColor(primary)
            c.rect(cx, cy-2.2, card_w, 2.2, fill=1, stroke=0)

            c.setFillColor(muted)
            c.setFont("Helvetica", 9)
            c.drawString(cx+10, cy-7*mm, label)

            c.setFillColor(fg)
            c.setFont("Helvetica-Bold", 16)
            txt = f"{val:.1f}" if isinstance(val, float) else f"{val}"
            c.drawString(cx+10, cy-14*mm, txt)

    def mini_bars(title: str, items: List[Dict[str, Any]], top_left: Tuple[float, float]):
        """Mini gráfico de barras horizontal (simples e rápido)."""
        tx, ty = top_left
        c.setFillColor(fg)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(tx, ty, title)
        ty -= 6*mm

        box_w = w - 32*mm
        box_h = 42*mm
        c.setFillColor(surface)
        c.setStrokeColor(border)
        c.setLineWidth(1)
        c.roundRect(tx, ty-box_h, box_w, box_h, 7, fill=1, stroke=1)

        max_v = max([int(it.get("count", 0)) for it in items] + [1])
        bar_x = tx + 10
        bar_y = ty - 12
        row_h = 7.5*mm
        usable_w = box_w - 80

        c.setFont("Helvetica", 8)
        for i, it in enumerate(items[:5]):
            label = str(it.get("status", "-"))[:18]
            v = int(it.get("count", 0))
            y = bar_y - i*row_h

            c.setFillColor(muted)
            c.drawString(bar_x, y, label)

            c.setFillColor(border)
            c.rect(bar_x + 50, y-2, usable_w, 6, fill=1, stroke=0)

            c.setFillColor(primary)
            bw = usable_w * (v / max_v)
            c.rect(bar_x + 50, y-2, bw, 6, fill=1, stroke=0)

            c.setFillColor(fg)
            c.drawRightString(tx + box_w - 10, y, str(v))

        return ty - box_h - 8*mm

    def simple_table(title: str, rows: List[List[Any]], col_widths: List[float], top_left: Tuple[float,float]):
        tx, ty = top_left
        c.setFillColor(fg)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(tx, ty, title)
        ty -= 6*mm

        table = Table(rows, colWidths=col_widths)
        style = TableStyle([
            ("BACKGROUND",(0,0),(-1,0), colors.Color(primary.red, primary.green, primary.blue, alpha=0.12)),
            ("TEXTCOLOR",(0,0),(-1,0), fg),
            ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
            ("FONTSIZE",(0,0),(-1,0),9),
            ("BACKGROUND",(0,1),(-1,-1), bg),
            ("TEXTCOLOR",(0,1),(-1,-1), fg),
            ("FONTSIZE",(0,1),(-1,-1),8),
            ("GRID",(0,0),(-1,-1),0.4, border),
            ("LEFTPADDING",(0,0),(-1,-1),6),
            ("RIGHTPADDING",(0,0),(-1,-1),6),
            ("TOPPADDING",(0,0),(-1,-1),4),
            ("BOTTOMPADDING",(0,0),(-1,-1),4),
        ])
        for r in range(1, len(rows)):
            if r % 2 == 0:
                style.add("BACKGROUND", (0, r), (-1, r), surface)
        table.setStyle(style)
        tw, th = table.wrapOn(c, w-32*mm, h)
        table.drawOn(c, tx, ty - th)
        return ty - th - 8*mm

    # -------- Page 1: resumo --------
    page_no = 1
    header("Relatório Executivo")
    kpis = _safe_get(summary, "kpis", default={}) or {}
    kpi_cards(kpis, h-50*mm)

    # distribuição por status
    dist = summary.get("status_distribution") or []
    y_after = mini_bars("Distribuição por Status", dist, (16*mm, h-98*mm))

    # top serviços
    top_serv = summary.get("top_servicos") or []
    serv_rows = [["Serviço", "Qtd"]]
    for it in top_serv[:8]:
        serv_rows.append([str(it.get("servico_nome","-"))[:42], int(it.get("count",0))])
    y_after2 = simple_table("Top Serviços", serv_rows, [90*mm, 25*mm], (16*mm, y_after))

    footer(page_no)
    c.showPage()
    page_no += 1

    # -------- Page 2: setores + críticos --------
    header("Detalhamento")
    top_set = summary.get("top_setores") or []
    set_rows = [["Setor", "Qtd"]]
    for it in top_set[:10]:
        set_rows.append([str(it.get("setor_nome","-"))[:42], int(it.get("count",0))])
    y = simple_table("Top Setores", set_rows, [90*mm, 25*mm], (16*mm, h-50*mm))

    crit = summary.get("critical") or []
    crit_rows = [["Quando", "Equip.", "Serviço", "Status"]]
    for it in crit[:10]:
        crit_rows.append([
            _fmt_dt(it.get("updated_at") or it.get("created_at")),
            str(it.get("equipamento_label") or it.get("equipamento_id") or "-")[:10],
            str(it.get("servico_nome") or it.get("servico_id") or "-")[:28],
            str(it.get("status") or "-")[:14],
        ])
    simple_table("Itens Críticos (amostra)", crit_rows, [32*mm, 25*mm, 70*mm, 25*mm], (16*mm, y))

    footer(page_no)
    c.showPage()

    c.save()
    return buf.getvalue()
