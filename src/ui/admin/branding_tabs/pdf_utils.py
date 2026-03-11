"""Utilitários compartilhados: helpers, dataclass Branding, builder de PDF executivo."""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


# ─── Compat helpers ───────────────────────────────────────────────────────────

def resp_data(res):
    if res is None:
        return None
    if hasattr(res, "data"):
        return getattr(res, "data")
    if isinstance(res, dict):
        return res.get("data")
    return None


def safe_float(x, default=0.0) -> float:
    try:
        return default if x is None else float(x)
    except Exception:
        return default


def safe_int(x, default=0) -> int:
    try:
        return default if x is None else int(float(x))
    except Exception:
        return default


def pct_change(new: float, old: float) -> Optional[float]:
    old = safe_float(old, 0.0)
    new = safe_float(new, 0.0)
    if old == 0.0:
        return None
    return ((new - old) / old) * 100.0


# ─── Branding DB ──────────────────────────────────────────────────────────────

def load_branding(sb, tenant_id: str) -> Dict[str, Any]:
    res = sb.table("tenant_branding").select("*").eq("tenant_id", tenant_id).maybe_single().execute()
    return resp_data(res) or {}


def save_branding(sb, tenant_id: str, payload: Dict[str, Any]) -> None:
    sb.table("tenant_branding").upsert({**payload, "tenant_id": tenant_id}).execute()


# ─── Branding dataclass ───────────────────────────────────────────────────────

@dataclass
class Branding:
    company_name: str
    logo_url: Optional[str]
    primary_color: str
    accent_color: str
    footer_note: str

    @classmethod
    def from_db(cls, db: Dict[str, Any]) -> "Branding":
        return cls(
            company_name=(db.get("company_name") or "AgroSafra").strip(),
            logo_url=(db.get("logo_url") or "").strip() or None,
            primary_color=(db.get("primary_color") or "#FFD100").strip(),
            accent_color=(db.get("accent_color") or "#7F1D1D").strip(),
            footer_note=(db.get("footer_note") or "Relatório gerado automaticamente.").strip(),
        )


# ─── Chart helper ─────────────────────────────────────────────────────────────

def make_bar_chart_png(
    labels: List[str],
    values: List[float],
    title: str,
    xlabel: str = "",
    ylabel: str = "",
    max_items: int = 10,
) -> bytes:
    import matplotlib.pyplot as plt

    labels = labels[:max_items]
    values = values[:max_items]
    fig = plt.figure(figsize=(8.2, 3.8), dpi=150)
    ax = fig.add_subplot(111)
    ax.bar(range(len(values)), values)
    ax.set_title(title)
    if xlabel:
        ax.set_xlabel(xlabel)
    if ylabel:
        ax.set_ylabel(ylabel)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=25, ha="right")
    ax.grid(True, axis="y", linestyle="--", alpha=0.3)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def hex_to_rgb(hex_color: str) -> Tuple[float, float, float]:
    h = (hex_color or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join([c * 2 for c in h])
    if len(h) != 6:
        return (0.9, 0.9, 0.9)
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)


# ─── PDF builder ──────────────────────────────────────────────────────────────

def build_executive_pdf(
    summary: Dict[str, Any],
    branding: Branding,
    period_label: str,
    tenant_label: str,
    show_percent: bool = True,
) -> bytes:
    """Gera PDF executivo: capa, insights, charts, evolução semanal."""
    import io as _io
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        Image, PageBreak, KeepTogether,
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_CENTER
    import requests

    primary = colors.Color(*hex_to_rgb(branding.primary_color))
    accent  = colors.Color(*hex_to_rgb(branding.accent_color))
    styles  = getSampleStyleSheet()

    h1    = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=20, leading=24, spaceAfter=10, textColor=primary, alignment=TA_LEFT)
    h2    = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=14, leading=18, spaceBefore=6, spaceAfter=6, textColor=accent, alignment=TA_LEFT)
    p     = ParagraphStyle("p",  parent=styles["BodyText"], fontSize=10.5, leading=14, alignment=TA_LEFT)
    small = ParagraphStyle("sm", parent=styles["BodyText"], fontSize=9.3,  leading=12, textColor=colors.grey, alignment=TA_LEFT)

    buf = _io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.6*cm, rightMargin=1.6*cm,
                            topMargin=1.6*cm, bottomMargin=1.6*cm,
                            title="Relatório Executivo", author=branding.company_name)
    story = []

    # ── Capa ──────────────────────────────────────────────────────────────────
    story += [Spacer(1, 0.6*cm), Paragraph(branding.company_name, h1),
              Paragraph("Relatório Executivo", h2), Spacer(1, 0.4*cm),
              Paragraph(f"<b>Período:</b> {period_label}", p),
              Paragraph(f"<b>{tenant_label}</b>", small), Spacer(1, 0.9*cm)]

    if branding.logo_url:
        try:
            r = requests.get(branding.logo_url, timeout=8)
            r.raise_for_status()
            img = Image(_io.BytesIO(r.content), width=10.5*cm, height=5.0*cm)
            img.hAlign = "LEFT"
            story += [img, Spacer(1, 0.6*cm)]
        except Exception:
            story.append(Paragraph("Logo não pôde ser carregado.", small))

    kpis  = summary.get("kpis") or summary.get("resumo") or {}
    total = safe_int(kpis.get("total") or summary.get("total") or summary.get("total_registros"))
    concl = safe_int(kpis.get("concluidos") or kpis.get("finalizados") or summary.get("concluidos"))
    pend  = safe_int(kpis.get("pendentes") or summary.get("pendentes"))
    trav  = safe_int(kpis.get("travados") or summary.get("travados"))

    rows = [["Indicador", "Valor"]]
    for label, val in [("Total", total), ("Concluídos", concl), ("Pendentes", pend), ("Travados", trav)]:
        if val:
            rows.append([label, str(val)])

    if len(rows) > 1:
        t = Table(rows, colWidths=[7.8*cm, 7.2*cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), primary), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
            ("BACKGROUND", (0, 1), (-1, -1), colors.whitesmoke),
            ("FONTSIZE", (0, 0), (-1, -1), 10), ("PADDING", (0, 0), (-1, -1), 6),
        ]))
        story += [Paragraph("Resumo do período", h2), t]

    story += [Spacer(1, 0.8*cm), Paragraph(branding.footer_note, small), PageBreak()]

    # ── Insights ──────────────────────────────────────────────────────────────
    story.append(Paragraph("Principais insights", h1))
    insights: List[str] = []

    backend_insights = summary.get("insights") or summary.get("principais_insights")
    if isinstance(backend_insights, list):
        for it in backend_insights:
            txt = it.strip() if isinstance(it, str) else (it.get("text") or it.get("insight") or "").strip()
            if txt:
                insights.append(txt)

    comp = summary.get("comparativos") or summary.get("deltas") or {}
    for campo, label in [("travados", "Travados"), ("concluidos", "Concluídos")]:
        old = comp.get(f"{campo}_anterior") or comp.get(f"{campo}_prev")
        new = comp.get(f"{campo}_atual") or comp.get(f"{campo}_curr") or locals().get(campo[:4])
        if old is not None and new is not None:
            ch = pct_change(safe_float(new), safe_float(old))
            if ch is not None:
                insights.append(f"{label} {'subiram' if ch >= 0 else 'caíram'} {abs(ch):.1f}% no período.")

    k = summary.get("kpis") or {}
    total_t = safe_float(k.get("total_tarefas"), 0.0)
    concl_t = safe_float(k.get("concluidas"), 0.0)
    trav_t  = safe_float(k.get("travadas"), 0.0)
    if total_t > 0:
        insights.append(f"Conclusão no período: {int(concl_t)}/{int(total_t)} ({concl_t/total_t*100:.1f}%).")
        insights.append("Nenhuma tarefa travada no período." if trav_t == 0 else
                        f"Tarefas travadas: {int(trav_t)} ({trav_t/total_t*100:.1f}%).")
        dist = summary.get("status_distribution") or []
        if isinstance(dist, list):
            pend_item = next((d.get("count") for d in dist if isinstance(d, dict)
                              and str(d.get("status", "")).lower() == "pendente"), None)
            if pend_item is not None:
                pend_t = safe_float(pend_item, 0.0)
                insights.append(f"Pendentes: {int(pend_t)} ({pend_t/total_t*100:.1f}%).")

    weekly = summary.get("weekly") or []
    if isinstance(weekly, list):
        wk = sorted(
            [(safe_int(w.get("semana"), -1), w) for w in weekly
             if isinstance(w, dict) and w.get("semana") is not None and safe_int(w.get("semana"), -1) >= 0],
            key=lambda x: x[0],
        )
        if len(wk) >= 2:
            s_prev, w_prev = wk[-2]
            s_last, w_last = wk[-1]
            pct_prev = safe_float(w_prev.get("pct_done"), 0.0) * 100
            pct_last = safe_float(w_last.get("pct_done"), 0.0) * 100
            done_last = safe_int(w_last.get("done_count"), 0)
            tot_last  = safe_int(w_last.get("total_count"), 0)
            delta = pct_last - pct_prev
            insights.append(
                f"Última semana (Semana {s_last}): {pct_last:.1f}% concluído ({done_last}/{tot_last}). "
                f"Variação vs semana anterior: {'+' if delta >= 0 else ''}{delta:.1f} pp."
            )

    def _best_by_pct(items, name_keys):
        if not isinstance(items, list):
            return None
        best_name, best_pct = None, -1.0
        for it in items:
            if not isinstance(it, dict) or it.get("pct_done") is None:
                continue
            pct_ = safe_float(it.get("pct_done"), 0.0)
            name = next((str(it[k_]) for k_ in name_keys if it.get(k_)), None)
            if name and pct_ > best_pct:
                best_name, best_pct = name, pct_
        return (best_name, best_pct) if best_name else None

    for key, name_keys, label in [
        ("top_servicos", ["servico_nome", "nome", "label", "servico"], "Serviço"),
        ("top_setores",  ["setor_nome", "nome", "label", "setor"],    "Setor"),
        ("top_grupos",   ["grupo_nome", "nome", "label", "grupo"],    "Grupo"),
    ]:
        best = _best_by_pct(summary.get(key), name_keys)
        if best and best[1] > 0:
            insights.append(f"{label} com melhor taxa de conclusão: <b>{best[0]}</b> ({best[1]*100:.1f}%).")

    if not insights:
        insights.append("Sem insights automáticos disponíveis para este recorte.")

    for s in insights[:8]:
        story += [Paragraph(f"• {s}", p), Spacer(1, 0.08*cm)]
    story.append(Spacer(1, 0.5*cm))

    # ── Charts ────────────────────────────────────────────────────────────────
    def _extract_top(key):
        items = summary.get(key) or []
        labels_out, qtds, pcts = [], [], []
        for it in (items if isinstance(items, list) else []):
            if isinstance(it, dict):
                lbl = next((str(it[k]) for k in ["label","nome","servico_nome","setor_nome","grupo_nome","servico","setor","grupo"] if it.get(k)), "").strip()
                qtd  = safe_float(it.get("qtd") or it.get("quantidade") or it.get("count"), 0.0)
                pctd = safe_float(it.get("pct_done") or it.get("percent_done") or it.get("pct_concluido") or it.get("pct"), 0.0)
                if lbl:
                    labels_out.append(lbl); qtds.append(qtd); pcts.append(pctd)
            elif isinstance(it, (list, tuple)) and len(it) >= 2:
                labels_out.append(str(it[0])); qtds.append(safe_float(it[1])); pcts.append(safe_float(it[2]) if len(it) >= 3 else 0.0)
        return labels_out, qtds, pcts

    charts_block: List[Any] = []
    for key, section_title in [("top_servicos", "Top Serviços"), ("top_setores", "Top Setores"), ("top_grupos", "Top Grupos")]:
        lbls, qtds, pcts = _extract_top(key)
        if lbls and qtds:
            charts_block.append(Spacer(1, 0.35*cm))
            charts_block.append(Paragraph(section_title, h2))
            png = make_bar_chart_png(lbls, qtds, f"{section_title} (quantidade)")
            if png:
                charts_block.append(Image(_io.BytesIO(png), width=16.2*cm, height=7.0*cm))
            if show_percent and any(pcts):
                png2 = make_bar_chart_png(lbls, [v * 100 for v in pcts], f"{section_title} (% concluído)", ylabel="%")
                if png2:
                    charts_block += [Spacer(1, 0.2*cm), Image(_io.BytesIO(png2), width=16.2*cm, height=7.0*cm)]

    # Evolução semanal
    if isinstance(weekly, list) and weekly:
        labels_w, pct_w, non_null = [], [], 0
        for w in weekly:
            if not isinstance(w, dict):
                continue
            semana = w.get("semana")
            si = safe_int(semana, -1)
            if si >= 0:
                labels_w.append(f"Semana {si}"); non_null += 1
            else:
                labels_w.append("Sem semana")
            pct_w.append(safe_float(w.get("pct_done"), 0.0) * 100)

        charts_block.append(Spacer(1, 0.35*cm))
        charts_block.append(Paragraph("Evolução semanal", h2))
        if non_null == 0 and len(labels_w) == 1:
            done = safe_int(weekly[0].get("done_count"), 0)
            tot  = safe_int(weekly[0].get("total_count"), 0)
            pct_ = safe_float(weekly[0].get("pct_done"), 0.0) * 100
            charts_block.append(Paragraph(f"Sem semana definida: <b>{done}/{tot}</b> concluídas (<b>{pct_:.1f}%</b>).", p))
        else:
            pngw = make_bar_chart_png(labels_w, pct_w, "Evolução semanal (% concluído)", ylabel="%")
            if pngw:
                charts_block.append(Image(_io.BytesIO(pngw), width=16.2*cm, height=7.0*cm))

    if charts_block:
        story.append(KeepTogether(charts_block))
    else:
        story.append(Paragraph("Sem dados de Top Serviços/Setores para este recorte.", small))

    story += [Spacer(1, 0.6*cm), Paragraph(branding.footer_note, small)]

    def _on_page(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawRightString(A4[0] - 1.6*cm, 1.0*cm, f"{branding.company_name} • {period_label}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    return buf.getvalue()
