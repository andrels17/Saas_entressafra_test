"""Matriz Operacional — visao por grupo com drill-down por setor.

Melhorias v2:
  1. Scope/permissoes — nao-admin veem apenas seus grupos/departamentos
  2. Tab "Resumo" dedicada com ranking visual por equipamento + barra de progresso
  3. Barra de progresso visual nos cards de grupo (tela de selecao)
  4. Filtro de semana na aba Matriz
  5. Observacoes inline no editor — expander por setor + campo no editor rapido
  6. _style_heatmap definida uma vez fora do loop de setores
  7. svc_ids_all calculado antes das tabs (sem dir() fragil)
  8. Barra de progresso no header do grupo
"""
from __future__ import annotations

import io
import time
from collections import defaultdict
from datetime import datetime, date, timezone
from src.utils.timezone import now_utc as _now_utc, now_brt as _now_brt
from src.utils.weeks import week_from_revisao as _week_from_revisao

import pandas as pd
import streamlit as st

from src.auth.roles import Role
from src.auth.scope import get_my_scope
from src.ui.core.styles import page_header as _ph
from src.ui.core.cache import bump_data_version, clear_cached_functions
from src.ui.components.forms import form_submit_button, validation_summary
from src.ui.components.confirmations import confirmation_panel
from src.utils import nav
from src.utils.supabase_helpers import (
    current_role,
    current_tenant_id,
    current_user_id,
    sb_for_user,
)
from src.ui.pages.matriz_sector import (
    build_change_preview_lines,
    build_sector_frame,
    sector_progress_label,
    sector_summary_metrics,
    summarize_sector_intelligence,
)
from src.ui.pages.matriz_runtime import (
    build_task_maps as _build_task_maps,
    bulk_update_tasks as _bulk_update_tasks,
    eq_label_map as _eq_label_map,
    filter_obs_map_for_sector as _filter_obs_map_for_sector,
    normalize_service_ids as _normalize_service_ids,
    risk_color as _risk_color,
    risk_score as _risk_score,
    sector_is_open as _sector_is_open,
    sector_set_open as _sector_set_open,
    svc_name_map as _svc_name_map,
    task_key as _task_key,
)






def _render_selection_context(
    *,
    is_group_view: bool,
    grupos: list[dict],
    grupo_id,
    departamento_id,
    is_admin: bool,
    dept_name_fn,
) -> tuple[bool, bool]:
    clear_dept = False
    show_all = False

    if is_group_view:
        gn = next((g.get("nome") for g in grupos if g.get("id") == grupo_id), "—")
        st.markdown(
            f'<div class="enterprise-chip"><strong>Grupo:</strong> {gn}</div>',
            unsafe_allow_html=True,
        )
        return False, False

    if departamento_id and is_admin:
        dn = dept_name_fn(departamento_id) or "(departamento)"
        st.markdown(
            f'<div class="enterprise-chip"><strong>Depto:</strong> {dn}</div>',
            unsafe_allow_html=True,
        )

    if is_admin:
        with st.popover("Ações", use_container_width=True):
            clear_dept = st.button(
                "Limpar depto",
                key="mtz_clear_dept",
                use_container_width=True,
            )
            show_all = st.button(
                "Ver todos",
                key="mtz_show_all",
                use_container_width=True,
            )

    return clear_dept, show_all







def _render_selection_context(
    *,
    is_group_view: bool,
    grupos: list[dict],
    grupo_id,
    departamento_id,
    is_admin: bool,
    dept_name_fn,
) -> tuple[bool, bool]:
    clear_dept = False
    show_all = False

    if is_group_view:
        gn = next((g.get("nome") for g in grupos if g.get("id") == grupo_id), "—")
        st.markdown(
            f'<div class="enterprise-chip"><strong>Grupo:</strong> {gn}</div>',
            unsafe_allow_html=True,
        )
        return False, False

    if departamento_id and is_admin:
        dn = dept_name_fn(departamento_id) or "(departamento)"
        st.markdown(
            f'<div class="enterprise-chip"><strong>Depto:</strong> {dn}</div>',
            unsafe_allow_html=True,
        )

    if is_admin:
        clear_dept = st.button(
            "Limpar depto",
            key="mtz_clear_dept",
            use_container_width=True,
        )
        show_all = st.button(
            "Ver todos",
            key="mtz_show_all",
            use_container_width=True,
        )

    return clear_dept, show_all





def __render_selection_context(
    *,
    is_group_view: bool,
    grupos: list[dict],
    grupo_id,
    departamento_id,
    is_admin: bool,
    dept_name_fn,
) -> tuple[bool, bool]:
    """Renderiza chips/contexto da seleção e retorna ações do usuário."""
    col_chip, col_actions = st.columns([1.6, 1.2])

    with col_chip:
        if is_group_view:
            gn = next((g.get("nome") for g in grupos if g.get("id") == grupo_id), "—")
            st.markdown(
                f'<div class="enterprise-chip"><strong>Grupo:</strong> {gn}</div>',
                unsafe_allow_html=True,
            )
        elif departamento_id and is_admin:
            dn = dept_name_fn(departamento_id) or "(departamento)"
            st.markdown(
                f'<div class="enterprise-chip"><strong>Depto:</strong> {dn}</div>',
                unsafe_allow_html=True,
            )

    with col_actions:
        if not is_group_view and is_admin:
            c1, c2 = st.columns(2)
            with c1:
                clear_dept = st.button("Limpar depto", key="mtz_clear_dept", use_container_width=True)
            with c2:
                show_all = st.button("Ver todos", key="mtz_show_all", use_container_width=True)
            return clear_dept, show_all

    return False, False

def _inject_css():
    st.markdown("""<style>
.enterprise-sticky{position:sticky;top:0;z-index:999;padding:12px 12px 10px 12px;
margin:0 0 12px 0;border-radius:18px;background:linear-gradient(180deg, rgba(18,18,18,.92), rgba(10,18,14,.88));
backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,.08);
box-shadow:0 10px 28px rgba(0,0,0,.35);}
.enterprise-title{font-size:1.1rem;font-weight:700;letter-spacing:.2px;margin:0}
.enterprise-sub{color:rgba(255,255,255,.68);font-size:.85rem;margin-top:2px}
.enterprise-chip-row{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
.enterprise-chip{display:inline-flex;align-items:center;gap:6px;padding:6px 10px;
border-radius:999px;border:1px solid rgba(255,255,255,.10);
background:rgba(255,255,255,.04);font-size:.82rem;color:rgba(255,255,255,.88)}
.enterprise-chip strong{color:rgba(255,255,255,.95)}
.enterprise-chip.ok{border-color:rgba(18,183,106,.35);background:rgba(18,183,106,.10)}
.enterprise-chip.warn{border-color:rgba(245,158,11,.35);background:rgba(245,158,11,.10)}
.enterprise-chip.bad{border-color:rgba(239,68,68,.35);background:rgba(239,68,68,.10)}
.enterprise-divider{height:1px;background:rgba(255,255,255,.08);margin:10px 0}

/* Cards de grupos — versão estável */
.mtz-card-grid{margin-top:8px}
.mtz-card-grid [data-testid="stButton"]{margin-bottom:2px}
.mtz-card-grid [data-testid="stButton"] button{
  width:100%;
  min-height:88px;
  padding:12px 14px;
  border-radius:14px;
  border:1px solid rgba(84,255,165,.14);
  background:linear-gradient(180deg, rgba(10,72,48,.52), rgba(7,40,28,.88));
  color:rgba(255,255,255,.96);
  box-shadow:0 6px 14px rgba(0,0,0,.18);
  transition:transform .12s ease, border-color .14s ease, box-shadow .14s ease;
  white-space:normal;
  line-height:1.42;
  font-weight:600;
  text-align:center;
}
.mtz-card-grid [data-testid="stButton"] button:hover{
  transform:translateY(-1px);
  border-color:rgba(110,255,180,.24);
  box-shadow:0 10px 20px rgba(0,0,0,.22);
}
.mtz-card-grid .mtz-pct-outer{
  margin:-6px 14px 12px 14px;
  height:8px !important;
  border-radius:0 0 999px 999px;
  background:rgba(255,255,255,.07);
  overflow:hidden;
  border:1px solid rgba(255,255,255,.05);
  box-shadow:inset 0 1px 2px rgba(0,0,0,.22);
}
.mtz-card-grid .mtz-pct-inner{
  border-radius:999px;
  box-shadow:0 0 10px rgba(255,255,255,.08);
}
.mtz-card-grid .mtz-pct-caption{
  margin-top:4px;
  font-size:.76rem;
  opacity:.72;
  text-align:center;
}

/* Painéis e inteligência */
.mtz-risk-badges{display:flex;flex-wrap:wrap;gap:8px;margin:4px 0 8px 0}
.mtz-risk-badge{display:inline-flex;align-items:center;padding:4px 9px;border-radius:999px;font-size:.78rem;font-weight:600;border:1px solid rgba(255,255,255,.08)}
.mtz-risk-badge.high{background:rgba(239,68,68,.14);border-color:rgba(239,68,68,.34);color:#fecaca}
.mtz-risk-badge.medium{background:rgba(245,158,11,.12);border-color:rgba(245,158,11,.32);color:#fde68a}
.mtz-risk-badge.low{background:rgba(34,197,94,.12);border-color:rgba(34,197,94,.30);color:#bbf7d0}
.mtz-sector-box{border-radius:18px;padding:12px 14px;margin:10px 0 14px 0;border:1px solid rgba(255,255,255,.08);box-shadow:0 8px 20px rgba(0,0,0,.18)}
.mtz-sector-box.high{background:linear-gradient(180deg, rgba(127,29,29,.24), rgba(0,0,0,0));border-color:rgba(239,68,68,.30)}
.mtz-sector-box.medium{background:linear-gradient(180deg, rgba(120,53,15,.18), rgba(0,0,0,0));border-color:rgba(245,158,11,.28)}
.mtz-sector-box.low{background:linear-gradient(180deg, rgba(20,83,45,.16), rgba(0,0,0,0));border-color:rgba(34,197,94,.24)}
.mtz-priority-panel{padding:12px 14px;border-radius:18px;border:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.03);margin:6px 0 14px 0;box-shadow:0 8px 20px rgba(0,0,0,.16)}
.mtz-priority-item{padding:8px 10px;border-radius:12px;margin:6px 0;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06)}
.mtz-kpi-panel{border-radius:16px;padding:10px 12px;border:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.03)}
</style>""", unsafe_allow_html=True)



def _pct_bar_html(pct: int, height: int = 6) -> str:
    color = _risk_color(pct)
    w = max(0, min(100, pct))
    h = max(height, 8)
    return (
        f'<div class="mtz-pct-outer" style="height:{h}px">'
        f'<div class="mtz-pct-inner" style="width:{w}%;background:{color};height:{h}px;transition:width .25s ease"></div>'
        f'</div>'
    )


def _fmt_duration_from_hours(hours) -> str:
    if hours is None:
        return "-"
    try:
        total_seconds = int(round(float(hours) * 3600))
    except Exception:
        return "-"
    if total_seconds < 0:
        total_seconds = 0
    days = total_seconds // 86400
    rem = total_seconds % 86400
    hrs = rem // 3600
    mins = (rem % 3600) // 60
    if days >= 1:
        return f"{days} dia{'s' if days != 1 else ''} e {hrs}h"
    if hrs >= 1:
        return f"{hrs} hora{'s' if hrs != 1 else ''}"
    return f"{mins} min"


def _sector_priority_sort_key(item: dict) -> tuple:
    risk_order = {"alto": 0, "medio": 1, "baixo": 2}
    return (
        risk_order.get(str(item.get("risk")), 3),
        -int(item.get("criticos", 0) or 0),
        -int(item.get("atrasadas_m", 0) or 0),
        int(item.get("pct", 0) or 0),
        str(item.get("setor_nome") or ""),
    )


def _build_group_sector_intelligence(
    *,
    equipamentos: list[dict],
    setor_to_services: dict,
    task_map: dict,
    atraso_dias: int,
    rev_start,
) -> list[dict]:
    intelligence: list[dict] = []
    for setor_nome in sorted(setor_to_services.keys(), key=lambda x: x.lower()):
        svs = sorted(
            setor_to_services[setor_nome],
            key=lambda x: (x.get("nome") or "").lower(),
        )
        svc_ids = [s.get("id") for s in svs if s.get("id")]
        if not svc_ids:
            continue
        intel = summarize_sector_intelligence(
            equipamentos=equipamentos,
            svc_ids=svc_ids,
            task_map=task_map,
            atraso_dias=int(atraso_dias),
            rev_start=rev_start,
        )
        intel["setor_nome"] = setor_nome
        intelligence.append(intel)
    return intelligence


def _build_automation_insights(
    *,
    sector_intelligence: list[dict],
    progresso_atual: float,
    meta_atual: float,
    critical_eq_count: int,
    no_start_eq_count: int,
) -> list[dict]:
    insights: list[dict] = []
    delta = round(float(progresso_atual) - float(meta_atual), 1)

    if delta <= -10:
        insights.append(
            {
                "nivel": "error",
                "titulo": "Ritmo abaixo da meta",
                "texto": f"O grupo está {abs(delta):.1f}% abaixo da meta linear da revisão.",
            }
        )
    elif delta < 0:
        insights.append(
            {
                "nivel": "warning",
                "titulo": "Leve atraso no ritmo",
                "texto": f"O grupo está {abs(delta):.1f}% abaixo da meta esperada.",
            }
        )
    else:
        insights.append(
            {
                "nivel": "success",
                "titulo": "Ritmo dentro da meta",
                "texto": f"O grupo está {delta:.1f}% acima da meta esperada.",
            }
        )

    delayed_mount = sum(int(item.get("atrasadas_m", 0) or 0) for item in sector_intelligence)
    if delayed_mount > 0:
        insights.append(
            {
                "nivel": "warning",
                "titulo": "Montagens atrasadas detectadas",
                "texto": f"Há {delayed_mount} montagem(ns) pendente(s) além do limite configurado.",
            }
        )

    high_risk = [item for item in sector_intelligence if item.get("risk") == "alto"]
    if high_risk:
        nomes = ", ".join(str(item.get("setor_nome")) for item in high_risk[:3])
        insights.append(
            {
                "nivel": "error",
                "titulo": f"{len(high_risk)} setor(es) em risco alto",
                "texto": f"Priorize: {nomes}" + ("..." if len(high_risk) > 3 else ""),
            }
        )

    if no_start_eq_count > 0:
        insights.append(
            {
                "nivel": "info",
                "titulo": "Frotas sem início",
                "texto": f"{no_start_eq_count} frota(s) ainda estão em 0% nesta revisão.",
            }
        )

    if critical_eq_count > 0:
        insights.append(
            {
                "nivel": "warning",
                "titulo": "Equipamentos críticos",
                "texto": f"{critical_eq_count} frota(s) estão abaixo de 50% de conclusão.",
            }
        )

    return insights


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
                for sid in svc_ids for f in ("etapa_d", "etapa_r", "etapa_m")
            )
            pct_sum += round((done / max(total_per, 1)) * 100)
            if done >= total_per:
                ok_eq += 1
        rows.append({"setor": setor, "ok_eq": ok_eq, "total_eq": len(eqs),
                     "pct_med": round(pct_sum / max(len(eqs), 1))})
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


def _build_pdf_tables(
    *,
    titulo,
    grupo_nome,
    resumo_df,
        sector_tables) -> bytes:
    from reportlab.lib import colors
    from src.utils.timezone import fmt_brt as _fmt_brt
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

    PAGE = landscape(A4)
    LMARGIN = RMARGIN = 1.25 * cm
    TMARGIN = 1.15 * cm
    BMARGIN = 1.20 * cm
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
        fontSize=7.7,
        leading=8.4,
        textColor=colors.white,
        fontName="Helvetica-Bold",
    )
    head_sub = ParagraphStyle(
        "head_sub",
        parent=small_style,
        alignment=TA_CENTER,
        fontSize=6.8,
        leading=7.4,
        textColor=colors.HexColor("#CBD5E1"),
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
        fontSize=7.8,
        leading=8.6,
        alignment=TA_CENTER,
        textColor=palette["soft"],
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
            cmds.extend([
                ("BACKGROUND", (0, 0), (-1, 0), palette["header"]),
                ("TEXTCOLOR", (0, 0), (-1, header_rows - 1), colors.white),
                ("FONTNAME", (0, 0), (-1, header_rows - 1), "Helvetica-Bold"),
            ])
        cmds.append(("ROWBACKGROUNDS", (0, zebra_from),
                    (-1, -1), [colors.white, palette["panel"]]))
        return cmds

    def _kpi_card(title: str, value_markup: str):
        card = Table(
            [[Paragraph(title, card_label)], [Paragraph(value_markup, card_value)]],
            colWidths=[pw / 4.0],
            rowHeights=[0.50 * cm, 0.80 * cm],
        )
        card.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.white),
            ("BOX", (0, 0), (-1, -1), 0.55, palette["line"]),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        return card

    def _summary_table(df: pd.DataFrame):
        cols = ["Equipamento", "Concluidos", "Total", "%"]
        if not isinstance(
                df, pd.DataFrame) or not all(
                c in df.columns for c in cols):
            return Paragraph("Sem dados.", small_style)

        view = df[cols].copy()
        view["Concluidos"] = view["Concluidos"].map(_safe_int)
        view["Total"] = view["Total"].map(_safe_int)
        view["%"] = view["%"].map(_int_pct)
        view = view.sort_values(["%", "Concluidos", "Equipamento"], ascending=[
                                False, False, True]).reset_index(drop=True)

        rows = [[
            Paragraph("<b>Equipamento</b>", cell_left),
            Paragraph("<b>Concluídos</b>", cell_left),
            Paragraph("<b>Total</b>", cell_left),
            Paragraph("<b>%</b>", cell_left),
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
            colWidths=[
                pw * 0.57,
                pw * 0.14,
                pw * 0.11,
                pw * 0.18],
            repeatRows=1)
        table.hAlign = "LEFT"
        style_cmds = _base_left_table_style(header_rows=1, zebra_from=1) + [
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ]
        for row_idx, pct in enumerate(view["%"].tolist(), start=1):
            style_cmds.extend([
                ("BACKGROUND", (3, row_idx), (3, row_idx), _pct_fill(_int_pct(pct))),
                ("TEXTCOLOR", (3, row_idx), (3, row_idx), _pct_color(_int_pct(pct))),
            ])
        table.setStyle(TableStyle(style_cmds))
        return table

    def _split_service_columns(df: pd.DataFrame):
        svc_cols = [
            c for c in df.columns if c not in (
                "Equipamento", "%", "Status")]
        groups = []
        order_map = {"D": 0, "R": 1, "M": 2}
        by_service = {}
        service_order = []
        for col in svc_cols:
            label = str(col)
            service_name = label
            suffix = None
            try:
                left, right = label.rsplit(" ", 1)
                if right in order_map:
                    service_name, suffix = left, right
            except Exception:
                pass
            if service_name not in by_service:
                by_service[service_name] = {
                    "name": service_name, "cols": [
                        None, None, None], "extras": []}
                service_order.append(service_name)
            if suffix is None:
                by_service[service_name]["extras"].append(col)
            else:
                by_service[service_name]["cols"][order_map[suffix]] = col
        for name in service_order:
            item = by_service[name]
            ordered = [c for c in item["cols"] if c] or item["extras"]
            groups.append((item["name"], ordered))
        return groups

    def _cell_heat_style(raw: str):
        val = str(raw or "").strip().upper()
        if val == "OK":
            return palette["ok_fill"], palette["ok"], "OK"
        if val in {"!", "PEND", "PENDENTE", "NOK", "NÃO", "NAO", "X"}:
            return palette["bad_fill"], palette["bad"], val
        return palette["empty_fill"], palette["muted"], ""

    def _build_sector_block(df: pd.DataFrame):
        groups = _split_service_columns(df)
        if not groups:
            return [Paragraph("Sem dados deste setor.", small_style)]

        equip_w = 5.2 * cm
        trio_w = 0.88 * cm
        max_groups = max(1, min(len(groups), int(
            (pw - equip_w) // (trio_w * 3)) or 1))
        chunks = [groups[i:i + max_groups]
                  for i in range(0, len(groups), max_groups)]
        blocks = []

        for chunk_idx, chunk in enumerate(chunks, start=1):
            data = []
            top = [Paragraph("<b>Equipamento</b>", head_top)]
            sub = [""]
            spans = [(0, 0, 0, 1)]
            cols_meta = [("Equipamento", None)]
            separators = []
            cur_col = 1

            for service_name, ordered_cols in chunk:
                normalized = list(ordered_cols)
                if len(normalized) == 3:
                    top.extend(
                        [Paragraph(f"<b>{service_name}</b>", head_top), "", ""])
                    sub.extend([
                        Paragraph("<b>D</b>", head_sub),
                        Paragraph("<b>R</b>", head_sub),
                        Paragraph("<b>M</b>", head_sub),
                    ])
                    spans.append((cur_col, 0, cur_col + 2, 0))
                    separators.append(cur_col + 2)
                    for col_name in normalized:
                        cols_meta.append((col_name, True))
                    cur_col += 3
                else:
                    top.append(Paragraph(f"<b>{service_name}</b>", head_top))
                    sub.append("")
                    for col_name in normalized:
                        cols_meta.append((col_name, True))
                    if len(normalized) == 1:
                        spans.append((cur_col, 0, cur_col, 1))
                    cur_col += len(normalized)

            data.extend([top, sub])
            view_cols = [name for name, _ in cols_meta]
            view = df[view_cols].copy().fillna("")

            for _, src in view.iterrows():
                row = [Paragraph(str(src["Equipamento"]), cell_left)]
                for col_name, _ in cols_meta[1:]:
                    _, _, text = _cell_heat_style(src[col_name])
                    row.append(Paragraph(text, cell_center))
                data.append(row)

            remaining = pw - equip_w
            matrix_cols = len(cols_meta) - 1
            matrix_w = max(
                0.82 * cm, min(0.94 * cm, remaining / max(matrix_cols, 1)))
            col_widths = [equip_w] + [matrix_w] * matrix_cols
            table = Table(data, colWidths=col_widths, repeatRows=2)
            table.hAlign = "LEFT"

            style_cmds = _base_left_table_style(header_rows=2, zebra_from=2) + [
                ("BACKGROUND", (0, 1), (-1, 1), palette["header_2"]),
                ("TEXTCOLOR", (0, 1), (-1, 1), colors.HexColor("#CBD5E1")),
                ("ALIGN", (0, 0), (-1, 1), "CENTER"),
                ("ALIGN", (0, 2), (0, -1), "LEFT"),
                ("ALIGN", (1, 2), (-1, -1), "CENTER"),
                ("SPAN", (0, 0), (0, 1)),
                ("LEFTPADDING", (1, 2), (-1, -1), 0),
                ("RIGHTPADDING", (1, 2), (-1, -1), 0),
                ("TOPPADDING", (1, 2), (-1, -1), 1),
                ("BOTTOMPADDING", (1, 2), (-1, -1), 1),
            ]
            for c1, r1, c2, r2 in spans[1:]:
                style_cmds.append(("SPAN", (c1, r1), (c2, r2)))
            for col in separators[:-1]:
                style_cmds.append(
                    ("LINEAFTER", (col, 0), (col, -1), 0.7, colors.HexColor("#94A3B8")))

            for row_i in range(2, len(data)):
                for col_i in range(1, len(cols_meta)):
                    bg, fg, text = _cell_heat_style(
                        view.iloc[row_i - 2, col_i])
                    style_cmds.extend([
                        ("BACKGROUND", (col_i, row_i), (col_i, row_i), bg),
                        ("TEXTCOLOR", (col_i, row_i), (col_i, row_i), fg),
                    ])
                    if text:
                        style_cmds.extend([
                            ("FONTNAME", (col_i, row_i), (col_i, row_i), "Helvetica-Bold"),
                            ("BOX", (col_i, row_i), (col_i, row_i), 0.35, fg),
                        ])

            table.setStyle(TableStyle(style_cmds))
            if len(chunks) > 1:
                blocks.append(
                    Paragraph(f"Bloco {chunk_idx}/{len(chunks)}", small_style))
                blocks.append(Spacer(1, 0.10 * cm))
            blocks.append(table)
            if chunk_idx < len(chunks):
                blocks.append(Spacer(1, 0.22 * cm))
        return blocks

    resumo_cols = ["Equipamento", "Concluidos", "Total", "%"]
    if isinstance(resumo_df, pd.DataFrame) and all(
            c in resumo_df.columns for c in resumo_cols):
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
        [
            [
                Paragraph(
                    "Relatório Operacional — Matriz",
                    title_style),
                Paragraph(
                    f'<font color="#6B7280">Data de emissão</font><br/><b>{emitido}</b>',
                    issued_style),
            ]],
        colWidths=[
            pw *
            0.76,
            pw *
            0.24],
    )
    header.hAlign = "LEFT"
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(header)
    story.append(
        HRFlowable(
            width="100%",
            thickness=0.75,
            color=palette["line"],
            spaceAfter=5,
            spaceBefore=1))

    meta = Table(
        [
            [
                Paragraph(
                    "Revisão", meta_label), Paragraph(
                    "Grupo", meta_label)], [
                        Paragraph(
                            titulo or "—", meta_value), Paragraph(
                                grupo_nome or "—", meta_value)], ], colWidths=[
                                    pw * 0.38, pw * 0.62], )
    meta.hAlign = "LEFT"
    meta.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 1), (-1, 1), 0.45, palette["line"]),
    ]))
    story.append(meta)
    story.append(Spacer(1, 0.24 * cm))

    cards = Table([[_kpi_card("Equipamentos",
                              str(total_eq)),
                    _kpi_card("Concluídos (100%)",
                  f'<font color="#16A34A">{eq_100}</font>'),
                    _kpi_card("Progresso médio",
                  f"{avg_pct}%"),
        _kpi_card("Sem início (0%)",
                  f'<font color="#EF4444">{eq_zero}</font>'),
    ]],
        colWidths=[pw / 4.0] * 4,
    )
    cards.hAlign = "LEFT"
    cards.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(cards)
    story.append(Spacer(1, 0.24 * cm))

    story.append(Paragraph("Resumo por equipamento", section_style))
    story.append(Spacer(1, 0.05 * cm))
    story.append(_summary_table(rv))

    for sector_name, sector_df in sector_tables:
        story.append(PageBreak())
        service_cols = [
            c for c in sector_df.columns if c not in (
                "Equipamento", "%", "Status")]
        ok_count = int(
            (sector_df[service_cols] == "OK").sum().sum()) if service_cols else 0
        total_cells = int(
            len(sector_df) *
            len(service_cols)) if service_cols else 0
        pct_general = int(
            round((ok_count / max(total_cells, 1)) * 100)) if total_cells else 0

        story.append(
            Paragraph(
                f"Detalhamento por setor — {sector_name}",
                section_style))
        story.append(
            Paragraph(
                f"Geral: <b>{pct_general}%</b> | Concluídos: <b>{ok_count}/{total_cells}</b>",
                sector_meta_style))
        story.append(Spacer(1, 0.16 * cm))
        for block in _build_sector_block(sector_df):
            story.append(block)

    def _footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(palette["muted"])
        canvas.drawString(
            LMARGIN,
            0.58 * cm,
            "D = desmontou   R = revisou   M = montou")
        canvas.drawRightString(
            PAGE[0] - RMARGIN,
            0.58 * cm,
            f"Página {
                canvas.getPageNumber()}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


@st.cache_data(ttl=60, show_spinner=False)
def _group_kpis(_tid, _rev_id, _ver="0"):
    _sb = sb_for_user()
    _gids = [
        g.get("id") for g in (
            _sb.table("equip_grupos").select("id").eq(
                "tenant_id",
                _tid).eq(
                "ativo",
                True).execute().data or []) if g.get("id")]
    if not _gids:
        return {}
    eq_rows = (
        _sb.table("equipamentos").select("id,grupo_id").eq(
            "tenant_id",
            _tid).eq(
            "ativo",
            True).in_(
                "grupo_id",
            _gids).execute().data) or []
    grp_eq = defaultdict(list)
    for r in eq_rows:
        if r.get("grupo_id") and r.get("id"):
            grp_eq[r["grupo_id"]].append(r["id"])
    tpl_rows = (
        _sb.table("grupo_servicos").select("grupo_id,servico_id").eq(
            "tenant_id",
            _tid).in_(
            "grupo_id",
            _gids).execute().data) or []
    grp_svc = defaultdict(set)
    for r in tpl_rows:
        if r.get("grupo_id") and r.get("servico_id"):
            grp_svc[r["grupo_id"]].add(r["servico_id"])
    all_eq = [eid for eids in grp_eq.values() for eid in eids]
    done = defaultdict(int)
    eq2g = {eid: gid for gid, eids in grp_eq.items() for eid in eids}
    for i in range(0, len(all_eq), 500):
        for t in ((_sb.table("tarefas_servico").select("equipamento_id,etapa_d,etapa_r,etapa_m")
                   .eq("tenant_id", _tid).eq("revisao_id", _rev_id).in_("equipamento_id", all_eq[i:i + 500])
                   .execute().data) or []):
            gid = eq2g.get(t.get("equipamento_id"))
            if gid:
                done[gid] += int(bool(t.get("etapa_d"))) + \
                    int(bool(t.get("etapa_r"))) + int(bool(t.get("etapa_m")))
    out = {}
    for gid in _gids:
        eqc = len(grp_eq.get(gid) or [])
        svc = len(grp_svc.get(gid) or set())
        pct = int(round((done.get(gid, 0) / max(eqc * svc * 3, 1))
                  * 100)) if (eqc > 0 and svc > 0) else 0
        out[gid] = {
            "eq_count": eqc, "svc_count": svc, "pct": max(
                0, min(
                    100, pct))}
    return out


@st.cache_data(ttl=60, show_spinner=False)
def _load_payload(_tid, _gid, _rid, _lim, _ver="0"):
    _sb = sb_for_user()
    _eqs = (
        _sb.table("equipamentos").select("id,frota,modelo").eq(
            "tenant_id",
            _tid) .eq(
            "grupo_id",
            _gid).eq(
                "ativo",
                True).order("frota").limit(
                    int(_lim)).execute().data) or []
    if not _eqs:
        return {"eqs": [], "s2s": {}, "all_s": [], "tarefas": []}
    _s2s, _all_s = _fetch_template(_sb, _tid, _gid)
    if not _all_s:
        return {"eqs": _eqs, "s2s": {}, "all_s": [], "tarefas": []}
    _tarefas = (
        _sb.table("tarefas_servico") .select(
            "id,equipamento_id,servico_id,status,semana,observacao,"
            "etapa_d,etapa_r,etapa_m,dt_inicio,dt_etapa_d,dt_etapa_r,dt_etapa_m") .eq(
            "tenant_id", _tid).eq(
                "revisao_id", _rid) .in_(
                    "equipamento_id", [
                        e["id"] for e in _eqs]).execute().data) or []
    return {"eqs": _eqs, "s2s": _s2s, "all_s": _all_s, "tarefas": _tarefas}


def _fetch_template(sb, tenant_id, grupo_id):
    for select, setor_fn in [
        ("servico_id, servicos(id,nome,setor_id,setores(nome))",
         lambda sv: (sv.get("setores") or {}).get("nome") or "Setor"),
        ("servico_id, servicos(id,nome,setor)",
         lambda sv: sv.get("setor") or "Setor"),
    ]:
        try:
            tpl = (
                sb.table("grupo_servicos").select(select) .eq(
                    "tenant_id",
                    tenant_id).eq(
                    "grupo_id",
                    grupo_id).execute().data) or []
            s2s = defaultdict(list)
            all_s = []
            for r in tpl:
                sv = r.get("servicos") or {}
                sid = sv.get("id")
                if not sid:
                    continue
                s2s[setor_fn(sv)].append(sv)
                all_s.append(sv)
            if all_s:
                return s2s, all_s
        except Exception:
            pass
    tpl = (
        sb.table("grupo_servicos").select("servico_id") .eq(
            "tenant_id",
            tenant_id).eq(
            "grupo_id",
            grupo_id).execute().data) or []
    ids = [r.get("servico_id") for r in tpl if r.get("servico_id")]
    if not ids:
        return defaultdict(list), []
    svs = (sb.table("servicos").select("id,nome,setor")
           .eq("tenant_id", tenant_id).in_("id", ids).execute().data) or []
    s2s = defaultdict(list)
    all_s = []
    for sv in svs:
        sn = sv.get("setor") or "Setor"
        item = {"id": sv.get("id"), "nome": sv.get("nome")}
        s2s[sn].append(item)
        all_s.append(item)
    return s2s, all_s


@st.cache_data(ttl=300, show_spinner=False)
def _dept_name(_tid, _did, _ver="0"):
    if not _did:
        return ""
    try:
        row = (
            sb_for_user().table("departamentos").select("nome").eq(
                "tenant_id", _tid).eq(
                "id", _did).limit(1).execute().data)
        return (row[0].get("nome") or "") if row else ""
    except BaseException:
        return ""


@st.cache_data(ttl=300, show_spinner=False)
def _all_dept_names(_tid, _ver="0"):
    try:
        rows = sb_for_user().table("departamentos").select(
            "id,nome").eq("tenant_id", _tid).execute().data or []
        return {r["id"]: r.get("nome", "") for r in rows}
    except BaseException:
        return {}


def render_matriz():
    try:
        _inject_css()
        _ph("\u229e", "Matriz de Atividades",
            "Visao por Grupo com drill-down por Setor. Etapas D/R/M, tempos e exportacoes.")

        tenant_id = current_tenant_id()
        sb = sb_for_user()
        role = current_role()

        # Melhoria 1: scope
        dep_scope_ids, grp_scope_ids = get_my_scope(tenant_id)
        is_admin = Role.is_admin(role)

        st.session_state.setdefault("data_version", "0")
        st.session_state.setdefault("matriz_view", "select")
        st.session_state.setdefault("matriz_limit_eq", 120)
        st.session_state.setdefault("matriz_show_legend", False)
        st.session_state.setdefault("matriz_departamento_id", None)
        st.session_state.setdefault("matriz_atraso_dias", 7)

        revisoes = (
            sb.table("revisoes").select("id,titulo,status,created_at,data_inicio,semanas_total") .eq(
                "tenant_id",
                tenant_id).order(
                "created_at",
                desc=True).execute().data) or []

        gq = sb.table("equip_grupos").select("id,nome,departamento_id").eq(
            "tenant_id", tenant_id).eq("ativo", True).order("nome")
        if not is_admin and dep_scope_ids:
            gq = (
                gq.eq(
                    "departamento_id",
                    dep_scope_ids[0]) if len(dep_scope_ids) == 1 else gq.in_(
                    "departamento_id",
                    dep_scope_ids))
        grupos = gq.execute().data or []
        if not is_admin and grp_scope_ids:
            grupos = [g for g in grupos if g["id"] in grp_scope_ids]
        if not grupos:
            st.info("Nenhum grupo disponivel para o seu escopo.")
            return

        if "matriz_revisao_id" not in st.session_state:
            ativa = next(
                (r for r in revisoes if r.get("status") == "ativa"), None)
            st.session_state["matriz_revisao_id"] = (
                ativa["id"] if ativa else (
                    revisoes[0]["id"] if revisoes else None))
        if "matriz_grupo_id" not in st.session_state:
            st.session_state["matriz_grupo_id"] = grupos[0]["id"]

        hph = st.empty()

        # Header sticky inicial (tela de selecao)
        with hph.container():
            st.markdown(
                '<div class="enterprise-sticky">',
                unsafe_allow_html=True)
            c1, c2, c3, c4 = st.columns(
                [2.7, 1.1, 1.7, 1.5], vertical_alignment="bottom")
            with c1:
                st.markdown(
                    '<div class="enterprise-title">Matriz Operacional</div>',
                    unsafe_allow_html=True)
                st.markdown(
                    '<div class="enterprise-sub">Etapas D/R/M · Setores · Evolucao semanal · Tempos</div>',
                    unsafe_allow_html=True)
            with c2:
                _clear_dept, _show_all = _render_selection_context(
                    is_group_view=st.session_state.get("matriz_view") == "group",
                    grupos=grupos,
                    grupo_id=st.session_state.get("matriz_grupo_id"),
                    departamento_id=st.session_state.get("matriz_departamento_id"),
                    is_admin=is_admin,
                    dept_name_fn=lambda dep_id: _dept_name(
                        tenant_id,
                        dep_id,
                        st.session_state.get("data_version", "0"),
                    ),
                )
                if _clear_dept:
                    st.session_state["matriz_departamento_id"] = None
                    st.rerun()
                if _show_all:
                    st.session_state["matriz_grp_search"] = ""
                    st.session_state["matriz_departamento_id"] = None
                    st.rerun()
            with c3:
                rev_opts = [
                    (r.get("titulo") or f"Revisao {
                        r['id']}",
                        r["id"]) for r in revisoes if r.get("id")]
                if not rev_opts:
                    st.selectbox(
                        "Revisao",
                        ["Nenhuma revisao"],
                        disabled=True,
                        key="rev_pick_dis")
                else:
                    rlbls = [lbl for lbl, _ in rev_opts]
                    rmap = {lbl: rid for lbl, rid in rev_opts}
                    cur = next((lbl for lbl, rid in rev_opts if rid ==
                               st.session_state["matriz_revisao_id"]), rlbls[0])
                    pick = st.selectbox(
                        "Revisao",
                        rlbls,
                        index=rlbls.index(cur),
                        key="mtz_rev_pick")
                    st.session_state["matriz_revisao_id"] = rmap[pick]
            with c4:
                st.session_state["matriz_limit_eq"] = st.number_input(
                    "Limite eq.", min_value=20, max_value=500, value=int(
                        st.session_state["matriz_limit_eq"]), step=20, key="mtz_lim_pick")
                st.session_state["matriz_show_legend"] = st.toggle(
                    "Legenda", value=bool(st.session_state["matriz_show_legend"]), key="mtz_leg")
                if st.button(
                    "Recarregar",
                    key="mtz_reload",
                    use_container_width=True,
                ):
                    bump_data_version()
                    clear_cached_functions(
                        _load_payload,
                        _group_kpis,
                        _all_dept_names,
                        _build_task_maps,
                        _filter_obs_map_for_sector,
                        _normalize_service_ids,
                    )
                    st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

        # Tela de selecao — cards com barra de progresso (Melhoria 3)
        if st.session_state.get("matriz_view") != "group":
            revisao_id = st.session_state.get("matriz_revisao_id")
            kpis = _group_kpis(
                tenant_id, revisao_id, st.session_state.get(
                    "data_version", "0")) if revisao_id else {}

            # FIX #9: busca por nome OU departamento, com filtro de status
            sc1, sc2, sc3 = st.columns([2.2, 1.15, 1.15], vertical_alignment="bottom")
            with sc1:
                st.session_state.setdefault("matriz_grp_search", "")
                search = st.text_input(
                    "🔎 Buscar",
                    value=st.session_state["matriz_grp_search"],
                    placeholder="Grupo ou departamento…",
                    key="mtz_search_in")
                st.session_state["matriz_grp_search"] = search
            with sc2:
                _status_filter = st.selectbox(
                    "Status",
                    [
                        "Todos",
                        "🔴 Crítico (<50%)",
                        "🟡 Em andamento (50–79%)",
                        "🟢 Avançado (≥80%)",
                        "⬜ Sem dados"],
                    index=0,
                    key="mtz_status_filter")
            with sc3:
                _sort_by = st.selectbox("Ordenar",
                                        ["Nome",
                                         "% ↑ (mais atrasados)",
                                         "% ↓ (mais avançados)"],
                                        index=1,
                                        key="mtz_sort_by")

            q = (search or "").strip().lower()
            dep_id = st.session_state.get("matriz_departamento_id")

            dept_names = _all_dept_names(
                tenant_id, st.session_state.get(
                    "data_version", "0"))

            show_groups = [
                g for g in grupos if (
                    not dep_id or g.get("departamento_id") == dep_id) and (
                    (not q) or (
                        q in (
                            g.get("nome") or "").lower()) or (
                        q in (
                            dept_names.get(
                                g.get("departamento_id"),
                                "")).lower()))]

            # Filtro de status
            if _status_filter != "Todos":
                def _status_match(g):
                    p = int(kpis.get(g.get("id"), {}).get("pct", 0))
                    eq = int(kpis.get(g.get("id"), {}).get("eq_count", 0))
                    if _status_filter.startswith("🔴"):
                        return p < 50 and eq > 0
                    if _status_filter.startswith("🟡"):
                        return 50 <= p < 80
                    if _status_filter.startswith("🟢"):
                        return p >= 80
                    if _status_filter.startswith("⬜"):
                        return eq == 0
                    return True
                show_groups = [g for g in show_groups if _status_match(g)]

            # Ordenação
            if _sort_by.startswith("% ↑"):
                show_groups = sorted(
                    show_groups, key=lambda g: kpis.get(
                        g.get("id"), {}).get(
                        "pct", 0))
            elif _sort_by.startswith("% ↓"):
                show_groups = sorted(
                    show_groups,
                    key=lambda g: -
                    kpis.get(
                        g.get("id"),
                        {}).get(
                        "pct",
                        0))
            else:
                show_groups = sorted(
                    show_groups, key=lambda g: (
                        g.get("nome") or "").lower())

            if not show_groups:
                st.info("Nenhum grupo encontrado para os filtros selecionados.")

            st.markdown('<div class="mtz-card-grid">', unsafe_allow_html=True)
            for row_start in range(0, len(show_groups), 3):
                row_groups = show_groups[row_start:row_start + 3]
                cols = st.columns(3)
                for col_idx, g in enumerate(row_groups):
                    gid = g.get("id")
                    nome = g.get("nome") or str(gid)
                    info = kpis.get(gid, {})
                    pct = int(info.get("pct", 0))
                    eqc = int(info.get("eq_count", 0))
                    svc = int(info.get("svc_count", 0))
                    dept_lbl = dept_names.get(g.get("departamento_id"), "")
                    _icon = "🟢" if pct >= 80 else (
                        "🟡" if pct >= 50 else (
                            "🔴" if eqc > 0 else "⬜"))
                    _sub = f"{dept_lbl} · " if dept_lbl else ""
                    with cols[col_idx]:
                        if st.button(
                            f"{_icon} {nome}\n\n{_sub}{pct}%  ·  {eqc} equip.  ·  {svc} serviços",
                            key=f"mtz_card_{gid}",
                            help=f"Clique para abrir o grupo {nome}",
                        ):
                            st.session_state["matriz_grupo_id"] = gid
                            st.session_state["matriz_view"] = "group"
                            st.rerun()
                        st.markdown(_pct_bar_html(pct), unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
            return

        # ── Visao do grupo ──
        grupo_id = st.session_state["matriz_grupo_id"]
        revisao_id = st.session_state["matriz_revisao_id"]
        limit_eq = int(st.session_state["matriz_limit_eq"])
        if not revisao_id:
            st.warning("Nenhuma revisao selecionada.")
            return

        # FIX TROCA DE GRUPO: se o grupo mudou desde o último render,
        # limpa o cache de dados para garantir que _load_payload busque do
        # banco.
        _last_rendered_grupo = st.session_state.get(
            "_mtz_last_rendered_grupo_id")
        if _last_rendered_grupo != grupo_id:
            try:
                _load_payload.clear()
            except Exception:
                clear_cached_functions(_load_payload)
            st.session_state["_mtz_last_rendered_grupo_id"] = grupo_id
            # Limpa payload cacheado manualmente no session_state
            st.session_state.pop("_mtz_payload_cache", None)

        if not is_admin and grp_scope_ids and grupo_id not in grp_scope_ids:
            st.warning("Voce nao tem acesso a este grupo.")
            if st.button("Voltar", key="mtz_back_noaccess"):
                st.session_state["matriz_view"] = "select"
                st.rerun()
            return

        rev_row = next(
            (r for r in revisoes if r.get("id") == revisao_id), None)
        titulo = (rev_row.get("titulo") if rev_row else None) or "Revisao"
        grupo_nome = next(
            (g.get("nome") for g in grupos if g.get("id") == grupo_id),
            str(grupo_id))

        if st.session_state.get("matriz_show_legend"):
            st.markdown(
                "**Legenda:** pendente · em andamento · concluido · travado · nao aplica")

        # Carrega payload — usa cache manual no session_state keyed por grupo_id
        # para garantir que troca de grupo sempre busca dados corretos do
        # banco.
        _payload_cache = st.session_state.get("_mtz_payload_cache") or {}
        _payload_key = (str(tenant_id), str(grupo_id), str(revisao_id), str(
            limit_eq), str(st.session_state.get("data_version", "0")))
        if _payload_cache.get("key") != str(_payload_key):
            _payload_cache = {
                "key": str(_payload_key),
                "data": _load_payload(
                    tenant_id,
                    grupo_id,
                    revisao_id,
                    limit_eq,
                    st.session_state.get(
                        "data_version",
                        "0")),
            }
            st.session_state["_mtz_payload_cache"] = _payload_cache
        payload = _payload_cache["data"]
        eqs = payload.get("eqs") or []
        if not eqs:
            st.info("Nenhum equipamento no grupo.")
            if st.button("Voltar", key="mtz_back_noeq"):
                st.session_state["matriz_view"] = "select"
                st.rerun()
            return

        eq_ids = [e["id"] for e in eqs]
        # eq_label: descricao completa — Resumo e PDF
        eq_label = {
            e["id"]: f"{
                e.get(
                    'frota',
                    '')} — {
                e.get('modelo') or ''}".strip(" —") for e in eqs}
        # eq_label_short: apenas o numero/frota — Matriz, Tempos, Editor
        eq_label_short = {e["id"]: (str(e.get("frota") or "")).strip() or str(
            e.get("id", "")) for e in eqs}
        setor_to_services = payload.get("s2s") or {}
        all_services = payload.get("all_s") or []

        if not all_services:
            try:
                s2s2, all2 = _fetch_template(sb, tenant_id, grupo_id)
                if all2:
                    setor_to_services, all_services = s2s2, all2
                    bump_data_version()
                    clear_cached_functions(_load_payload, _group_kpis, _all_dept_names)
                else:
                    st.warning(
                        "Grupo sem Template configurado (Admin > Templates).")
                    if st.button("Voltar", key="mtz_back_notpl"):
                        st.session_state["matriz_view"] = "select"
                        st.rerun()
                    return
            except Exception:
                st.warning(
                    "Grupo sem Template configurado (Admin > Templates).")
                if st.button("Voltar", key="mtz_back_notpl2"):
                    st.session_state["matriz_view"] = "select"
                    st.rerun()
                return

        tarefas = payload.get("tarefas") or []
        task_map = {(t["equipamento_id"], t["servico_id"]): t for t in tarefas}

        # Melhoria 7: svc_ids_all antes das tabs
        svc_ids_all = [s.get("id") for s in all_services if s.get("id")]
        semanas_disp = sorted({int(t.get("semana") or 0)
                              for t in tarefas if t.get("semana")})

        # Semana sugerida: calculada a partir da data_inicio da revisão (BRT)
        _rev_data_inicio = None
        _rev_semanas_total = None
        try:
            if rev_row and rev_row.get("data_inicio"):
                _rev_data_inicio = date.fromisoformat(
                    str(rev_row["data_inicio"])[:10])
            _rev_semanas_total = int(
                rev_row.get("semanas_total") or 0) or None if rev_row else None
        except Exception:
            pass
        _semana_sugerida = _week_from_revisao(
            _now_brt().date(), _rev_data_inicio, _rev_semanas_total)

        total_per_eq = max(len(all_services), 1) * 3
        resumo_rows = []
        tok_g = 0
        eq100_g = 0
        for e in eqs:
            done = sum(int(bool((task_map.get((e["id"], s.get("id"))) or {}).get(
                f))) for s in all_services if s.get("id") for f in ("etapa_d", "etapa_r", "etapa_m"))
            pct = round((done / max(total_per_eq, 1)) * 100)
            resumo_rows.append({"Score": _risk_score(pct), "%": pct, "Equipamento": eq_label.get(
                e["id"], str(e.get("id"))), "Concluidos": int(done), "Total": int(total_per_eq)})
            tok_g += done
            if done >= (len(all_services) * 3):
                eq100_g += 1
        resumo_df = pd.DataFrame(resumo_rows)
        if not resumo_df.empty:
            resumo_df = resumo_df.sort_values(["Score", "%", "Equipamento"], ascending=[
                                              False, True, True]).reset_index(drop=True)

        pct_geral = round(
            (tok_g / max(len(eqs) * len(all_services) * 3, 1)) * 100)
        setor_rows = _compute_setor_ok_counts(eqs, setor_to_services, task_map)
        # Header com barra de progresso
        with hph.container():
            st.markdown(
                '<div class="enterprise-sticky">',
                unsafe_allow_html=True)
            cL, cR = st.columns([6, 1], vertical_alignment="center")
            with cL:
                st.markdown(
                    f'<div class="enterprise-title">{grupo_nome}</div>',
                    unsafe_allow_html=True)
                st.markdown(
                    f'<div class="enterprise-sub">Revisão: <b>{titulo}</b>  ·  Equip.: <b>{
                        len(eqs)}</b>  ·  Geral: <b>{pct_geral}%</b>  ·  100%: <b>{eq100_g}/{
                        len(eqs)}</b></div>', unsafe_allow_html=True)
                st.markdown(
                    _pct_bar_html(
                        pct_geral,
                        height=8),
                    unsafe_allow_html=True)
            with cR:
                if st.button(
                    "← Voltar",
                    key="mtz_back_hdr",
                    use_container_width=True,
                ):
                    st.session_state["matriz_view"] = "select"
                    st.rerun()
            # FIX #6: chips clicáveis — cada um é um botão que pula para o
            # setor na aba Matriz
            if setor_rows:
                st.markdown(
                    '<div class="enterprise-divider"></div>',
                    unsafe_allow_html=True)
                st.markdown(
                    '<div class="enterprise-chip-row" style="flex-wrap:wrap;gap:6px;display:flex;margin-top:6px">',
                    unsafe_allow_html=True)
                chip_cols = st.columns(min(len(setor_rows[:12]), 6))
                for ci, r in enumerate(setor_rows[:12]):
                    ratio = r["ok_eq"] / max(r["total_eq"], 1)
                    icon = "🟢" if ratio >= 0.8 else (
                        "🟡" if ratio >= 0.5 else "🔴")
                    lbl = f"{icon} {r['setor']} {r['ok_eq']}/{r['total_eq']}"
                    with chip_cols[ci % len(chip_cols)]:
                        if st.button(
                                lbl, key=f"chip_setor_{ci}_{r['setor']}".replace(" ", "_"), use_container_width=True, help=f"{r['setor']}: {r['pct_med']}% médio · {r['ok_eq']}/{r['total_eq']} equip. 100%"):
                            st.session_state["mtz_chip_jump"] = r["setor"]
                            _sector_set_open(revisao_id, grupo_id, r["setor"], True)
                            st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)


        group_atraso_dias = int(st.session_state.get("matriz_atraso_dias", 7) or 7)
        group_rev_start = pd.to_datetime(
            (rev_row or {}).get("data_inicio") or (rev_row or {}).get("created_at"),
            errors="coerce",
            utc=True,
        )
        if pd.isna(group_rev_start):
            group_rev_start = pd.Timestamp(_now_utc()).normalize()

        analytics_sector_intelligence = _build_group_sector_intelligence(
            equipamentos=eqs,
            setor_to_services=setor_to_services,
            task_map=task_map,
            atraso_dias=group_atraso_dias,
            rev_start=group_rev_start,
        )
        analytics_priority_sorted = sorted(
            analytics_sector_intelligence,
            key=_sector_priority_sort_key,
        )

        elapsed_days = 0
        try:
            elapsed_days = max(
                0,
                int((pd.Timestamp(_now_utc()).tz_convert("UTC") - group_rev_start).days),
            )
        except Exception:
            elapsed_days = 0
        current_week_no = int(elapsed_days // 7 + 1)
        total_weeks_plan = int((rev_row or {}).get("semanas_total") or current_week_no or 1)
        expected_pct_now = round(min(100.0, (current_week_no / max(total_weeks_plan, 1)) * 100), 1)
        progresso_atual_pct = float(pct_geral)
        delta_vs_expected_now = round(progresso_atual_pct - expected_pct_now, 1)

        critical_eq_count = int((resumo_df["%"] < 50).sum()) if not resumo_df.empty else 0
        no_start_eq_count = int((resumo_df["%"] == 0).sum()) if not resumo_df.empty else 0

        automation_insights = _build_automation_insights(
            sector_intelligence=analytics_sector_intelligence,
            progresso_atual=progresso_atual_pct,
            meta_atual=expected_pct_now,
            critical_eq_count=critical_eq_count,
            no_start_eq_count=no_start_eq_count,
        )

        tab_resumo, tab_matriz, tab_evolucao, tab_analytics, tab_tempos, tab_editor, tab_exportar = st.tabs([
            "📊 Resumo", "⚙️ Matriz", "📈 Evolução", "🧠 Analytics", "⏱️ Tempos", "✏️ Editar célula", "⬇️ Exportar"])

        # FIX #3 e #8: pré-computar dados de export ANTES das tabs
        # Assim Exportar funciona mesmo sem o usuário ter visitado Matriz ou
        # Tempos

        # FIX GRUPO: invalidar bytes de PDF cacheados ANTES das tabs,
        # para garantir que trocar de grupo sempre gera um novo PDF.
        _early_signature = (str(tenant_id), str(grupo_id), str(revisao_id))
        if st.session_state.get("_mtz_pdf_grupo_sig") != _early_signature:
            st.session_state.pop("mtz_pdf_export_bytes", None)
            st.session_state.pop("mtz_pdf_export_signature", None)
            st.session_state["_mtz_pdf_grupo_sig"] = _early_signature

        sector_tables_for_export = []
        for _sn in sorted(setor_to_services.keys()):
            _svs = sorted(
                setor_to_services[_sn],
                key=lambda x: (
                    x.get("nome") or "").lower())
            _sids = [s["id"] for s in _svs if s.get("id")]
            _snames = [s.get("nome") or str(s.get("id"))
                       for s in _svs if s.get("id")]
            if not _sids:
                continue
            _rows = []
            for e in eqs:
                _row = {"Equipamento": eq_label_short[e["id"]]}
                for sid, sname in zip(_sids, _snames):
                    t = task_map.get((e["id"], sid)) or {}
                    _row[f"{sname} D"] = "OK" if t.get("etapa_d") else ""
                    _row[f"{sname} R"] = "OK" if t.get("etapa_r") else ""
                    _row[f"{sname} M"] = "OK" if t.get("etapa_m") else ""
                _rows.append(_row)
            if _rows:
                sector_tables_for_export.append((_sn, pd.DataFrame(_rows)))

        # FIX #8: pré-computar view_agg para CSV de tempos (independente de
        # visitar a aba)
        _view_agg_rows = []
        for e in eqs:
            for s in all_services:
                sid = s.get("id")
                sname = s.get("nome", "")
                if not sid:
                    continue
                t = task_map.get((e["id"], sid)) or {}
                _td = t.get("dt_etapa_d")
                _tr = t.get("dt_etapa_r")
                _tm = t.get("dt_etapa_m")

                def _hrs(a, b):
                    try:
                        ta = pd.to_datetime(a, utc=True)
                        tb = pd.to_datetime(b, utc=True)
                        return round(
                            (tb - ta).total_seconds() / 3600,
                            1) if pd.notna(ta) and pd.notna(tb) else None
                    except BaseException:
                        return None
                _view_agg_rows.append(
                    {
                        "Frota": eq_label.get(
                            e["id"], str(
                                e.get(
                                    "id", ""))), "Serviço": sname, "D→R (h)": _hrs(
                            _td, _tr), "R→M (h)": _hrs(
                            _tr, _tm), "D→M (h)": _hrs(
                                _td, _tm), })
        view_agg = pd.DataFrame(
            _view_agg_rows) if _view_agg_rows else pd.DataFrame()

        # ── TAB: RESUMO ──
        with tab_resumo:
            st.markdown("### Ranking de equipamentos por progresso")
            st.caption("Ordenado do mais atrasado para o mais adiantado.")
            if resumo_df.empty:
                st.info("Sem dados de resumo para esta revisão.")
            else:
                # KPIs rápidos no topo
                rk1, rk2, rk3, rk4 = st.columns(4)
                rk1.metric("Total equip.", len(resumo_df))
                rk2.metric(
                    "100% concluídos", int(
                        (resumo_df["%"] >= 100).sum()))
                rk3.metric("Progresso médio", f"{int(resumo_df['%'].mean())}%")
                rk4.metric("Sem início (0%)", int((resumo_df["%"] == 0).sum()))
                st.markdown("---")
                # Cards visuais — única representação, sem tabela duplicada
                for _, row in resumo_df.iterrows():
                    pct_r = int(row["%"])
                    color = _risk_color(pct_r)
                    c1r, c2r = st.columns([0.6, 0.4])
                    with c1r:
                        st.markdown(
                            f'<div style="font-size:.88rem;font-weight:600;margin-bottom:3px">{row["Equipamento"]}</div>'
                            f'<div style="background:rgba(255,255,255,.08);border-radius:4px;height:7px">'
                            f'<div style="width:{pct_r}%;background:{color};height:7px;border-radius:4px;transition:width .4s"></div></div>',
                            unsafe_allow_html=True)
                    with c2r:
                        _done_lbl = int(row["Concluidos"])
                        _tot_lbl = int(row["Total"])
                        _st_lbl = "✅ Concluído" if pct_r >= 100 else (
                            "🔴 Sem início" if pct_r == 0 else f"🟡 {pct_r}%")
                        st.markdown(
                            f'<div style="font-size:.82rem;color:rgba(255,255,255,.65);padding-top:3px">'
                            f'<span style="color:{color};font-weight:700">{pct_r}%</span>'
                            f'  ·  {_done_lbl}/{_tot_lbl} etapas'
                            f'  <span style="opacity:.6">{_st_lbl}</span></div>',
                            unsafe_allow_html=True)

        # ── TAB: MATRIZ ──
        with tab_matriz:
            st.markdown("### Drill-down por setor")
            st.caption(
                "Marque as etapas (D/R/M) direto na tabela. Setores 🔴 são prioridade — expanda para editar.")
            atraso_dias = group_atraso_dias
            fc1, fc2, fc3 = st.columns([1, 1.5, 1.5])
            with fc1:
                atraso_dias = st.number_input(
                    "Atraso (dias)",
                    min_value=1,
                    max_value=90,
                    value=atraso_dias,
                    step=1,
                    key="mtz_atraso_in",
                    help="Marca coluna M como atraso quando passou mais de X dias.")
                st.session_state["matriz_atraso_dias"] = int(atraso_dias)
            with fc2:
                # Melhoria 4: filtro de semana
                sem_opts = ["Todas as semanas"] + \
                    [f"Semana {s}" for s in semanas_disp]
                sem_pick = st.selectbox(
                    "Filtrar por semana",
                    sem_opts,
                    index=0,
                    key="mtz_sem_pick")
                semana_filtro = None if sem_pick == "Todas as semanas" else int(
                    sem_pick.split()[-1])
            with fc3:
                semana_lote = st.number_input(
                    "📅 Semana do apontamento",
                    min_value=0, max_value=99,
                    value=int(_semana_sugerida),
                    step=1, key="mtz_semana_lote",
                    help=f"Semana sugerida automaticamente ({_semana_sugerida}) com base na data de início da revisão. "
                    "Altere se estiver registrando uma etapa de outra semana. "
                    "Aplicada apenas em tarefas que ainda não têm semana definida."
                )

            rev_start = group_rev_start

            # FIX #6: chips clicáveis — se o usuário clicou num chip, pular
            # direto para aquele setor
            _chip_target = st.session_state.pop("mtz_chip_jump", None)

            sector_intelligence = []
            for _setor_nome in sorted(setor_to_services.keys(), key=lambda x: x.lower()):
                _svs_all = sorted(setor_to_services[_setor_nome], key=lambda x: (x.get("nome") or "").lower())
                _svc_ids_all = [s["id"] for s in _svs_all if s.get("id")]
                if semana_filtro is not None:
                    _svc_na_sem = {t["servico_id"] for t in tarefas if t.get("semana") == semana_filtro and t.get("servico_id")}
                    _svc_ids_all = [sid for sid in _svc_ids_all if sid in _svc_na_sem]
                if not _svc_ids_all:
                    continue
                _intel = summarize_sector_intelligence(
                    equipamentos=eqs,
                    svc_ids=_svc_ids_all,
                    task_map=task_map,
                    atraso_dias=int(atraso_dias),
                    rev_start=rev_start,
                )
                _intel["setor_nome"] = _setor_nome
                sector_intelligence.append(_intel)

            if sector_intelligence:
                _priority_sorted = sorted(
                    sector_intelligence,
                    key=_sector_priority_sort_key,
                )
                st.markdown('<div class="mtz-priority-panel">', unsafe_allow_html=True)
                st.markdown("#### 🔥 Prioridades agora")
                for _idx, _item in enumerate(_priority_sorted[:3], start=1):
                    st.markdown(
                        f'<div class="mtz-priority-item"><b>{_idx}. {_item["setor_nome"]}</b> · '
                        f'{_item["risk_icon"]} risco {_item["risk_label"]} · '
                        f'<b>{_item["pct"]}%</b> concluído · '
                        f'{_item["criticos"]} críticos · '
                        f'{_item["atrasadas_m"]} atraso(s) de montagem<br>'
                        f'<span style="opacity:.78">{_item["recommendation"]}</span></div>',
                        unsafe_allow_html=True,
                    )
                st.markdown('</div>', unsafe_allow_html=True)

            for setor_nome in sorted(
                    setor_to_services.keys(),
                    key=lambda x: x.lower()):
                svs = sorted(
                    setor_to_services[setor_nome],
                    key=lambda x: (
                        x.get("nome") or "").lower())
                svc_ids = [s["id"] for s in svs if s.get("id")]
                svc_names = [s.get("nome") or str(s.get("id"))
                             for s in svs if s.get("id")]
                if not svc_ids:
                    continue
                if semana_filtro is not None:
                    svc_na_sem = {t["servico_id"] for t in tarefas if t.get(
                        "semana") == semana_filtro and t.get("servico_id")}
                    svc_ids_v = [sid for sid in svc_ids if sid in svc_na_sem]
                    svc_names_v = [
                        svc_names[i] for i,
                        sid in enumerate(svc_ids) if sid in svc_na_sem]
                    if not svc_ids_v:
                        continue
                else:
                    svc_ids_v = svc_ids
                    svc_names_v = svc_names

                # Pré-calcular progresso para label e auto-expand
                _done_s, _tot_s, _pct_s, _lbl_exp = sector_progress_label(
                    equipamentos=eqs,
                    svc_ids=svc_ids_v,
                    task_map=task_map,
                    setor_nome=setor_nome,
                )

                # Lazy load real por setor: só renderiza o conteúdo quando aberto
                _auto_expand = (_pct_s == 0) or (setor_nome == _chip_target)
                if _auto_expand and not _sector_is_open(revisao_id, grupo_id, setor_nome):
                    _sector_set_open(revisao_id, grupo_id, setor_nome, True)

                _sector_open = _sector_is_open(revisao_id, grupo_id, setor_nome)
                _sector_intel = summarize_sector_intelligence(
                    equipamentos=eqs,
                    svc_ids=svc_ids_v,
                    task_map=task_map,
                    atraso_dias=int(atraso_dias),
                    rev_start=rev_start,
                )
                _risk_class = "high" if _sector_intel["risk"] == "alto" else ("medium" if _sector_intel["risk"] == "medio" else "low")

                st.markdown(f'<div class="mtz-sector-box {_risk_class}">', unsafe_allow_html=True)
                with st.container():
                    _head_l, _head_r = st.columns([0.78, 0.22])
                    with _head_l:
                        st.markdown(f"#### {_lbl_exp}")
                        st.markdown(
                            '<div class="mtz-risk-badges">'
                            f'<span class="mtz-risk-badge {_risk_class}">{_sector_intel["risk_icon"]} Risco {_sector_intel["risk_label"]}</span>'
                            f'<span class="mtz-risk-badge {"high" if _sector_intel["criticos"] else "low"}">Críticos: {_sector_intel["criticos"]}</span>'
                            f'<span class="mtz-risk-badge {"medium" if _sector_intel["em_andamento"] else "low"}">Em andamento: {_sector_intel["em_andamento"]}</span>'
                            f'<span class="mtz-risk-badge {"high" if _sector_intel["atrasadas_m"] else "low"}">Atraso M: {_sector_intel["atrasadas_m"]}</span>'
                            f'<span class="mtz-risk-badge {"medium" if _sector_intel["sem_inicio"] else "low"}">Sem início: {_sector_intel["sem_inicio"]}</span>'
                            '</div>',
                            unsafe_allow_html=True,
                        )
                        st.caption(_sector_intel["recommendation"])
                    with _head_r:
                        _toggle_label = "Ocultar setor" if _sector_open else "Abrir setor"
                        if st.button(
                                _toggle_label,
                            key=f"mtz_toggle_sector_{revisao_id}_{grupo_id}_{setor_nome}".replace(" ", "_"),
                            use_container_width=True,
                        ):
                            _sector_set_open(revisao_id, grupo_id, setor_nome, not _sector_open)
                            st.rerun()

                    if not _sector_open:
                        st.caption("Clique em **Abrir setor** para carregar a grade e editar apenas este setor.")
                        continue
                    df, col_meta, obs_map = build_sector_frame(
                        equipamentos=eqs,
                        svc_ids=svc_ids_v,
                        svc_names=svc_names_v,
                        task_map=task_map,
                        eq_label_short=eq_label_short,
                    )
                    if df.empty:
                        st.info("Sem dados para este setor.")
                        continue
                    df_display = df.set_index("_equip_id", drop=True)
                    svc_bool = [
                        c for c in df_display.columns if c not in (
                            "%", "Equipamento")]
                    tok_s, tc_s, pg, pm, eq_100s = sector_summary_metrics(df_display, svc_bool)
                    c1s, c2s, c3s = st.columns([1, 1, 2])
                    c1s.metric("Geral (ponderado)", f"{pg}%")
                    c2s.metric("Médio (frotas)", f"{pm}%")
                    with c3s:
                        _eq100_html = (
                            f' &nbsp;·&nbsp; <b style="color:#12B76A">{eq_100s}</b> 100%' if eq_100s > 0 else "")
                        st.markdown(
                            f'<div style="padding-top:8px;font-size:.82rem;color:rgba(255,255,255,.65)">'
                            f'{len(df)} eq &nbsp;·&nbsp; {len(svc_ids_v)} serviços &nbsp;·&nbsp; '
                            f'<b style="color:rgba(255,255,255,.9)">{tok_s}/{tc_s}</b> concluídas'
                            f'{_eq100_html}'
                            f'</div>'
                            f'{_pct_bar_html(pg, height=4)}',
                            unsafe_allow_html=True)

                    # Removida coluna "%" — o progresso já aparece no header
                    # acima; mantemos só "Equipamento"
                    df_display = df_display.drop(
                        columns=["%"], errors="ignore")
                    if "Status" not in df_display.columns:
                        df_display.insert(
                            0,
                            "Status",
                            df_display.apply(
                                lambda rw: "✓" if all(
                                    bool(
                                        rw.get(
                                            c,
                                            False)) for c in svc_bool) else "",
                                axis=1)) if svc_bool else None

                    # Observacoes com frota curta
                    if obs_map:
                        with st.expander(f"💬 Observações ({len(obs_map)})", expanded=False):
                            for key, obs_txt in obs_map.items():
                                eid_k, sid_k = key.split("__")
                                eq_n = eq_label_short.get(eid_k, eid_k)
                                _svc_names = _svc_name_map(svs)
                                svc_n = _svc_names.get(str(sid_k), sid_k)
                                st.markdown(
                                    f"**Frota {eq_n}** · {svc_n}: _{obs_txt}_")

                    _svc_names = _svc_name_map(svs)
                    kb = f"mat_ed_{revisao_id}_{grupo_id}_{setor_nome}".replace(
                        " ", "_")
                    mode = st.radio(
                        "Visualização", [
                            "Editar", "Visual"], horizontal=True, key=f"mtz_mode_{kb}")

                    if mode == "Visual":
                        days_since = int(
                            (pd.Timestamp(
                                _now_utc()) -
                                rev_start).days) if isinstance(
                            rev_start,
                            pd.Timestamp) else 0
                        df_vis = df_display.copy()
                        for c in svc_bool:
                            df_vis[c] = df_vis[c].apply(
                                lambda v: "OK" if bool(v) else "")
                        if days_since > atraso_dias:
                            for c in [
                                    c for c in svc_bool if str(c).strip().endswith(" M")]:
                                df_vis.loc[df_vis[c] == "", c] = "!"
                        st.dataframe(
                            df_vis.style.apply(
                                _style_heatmap,
                                axis=None),
                            use_container_width=True,
                            hide_index=True)
                        edited = None
                    else:
                        edited = st.data_editor(
                            df_display, key=kb, use_container_width=True, hide_index=True, column_config={
                                "Status": st.column_config.TextColumn(
                                    "✓", disabled=True, width="small"), "Equipamento": st.column_config.TextColumn(
                                    "Equipamento", disabled=True), **{
                                    col: st.column_config.CheckboxColumn(col) for col in svc_bool}}, disabled=[
                                "Status", "Equipamento"])

                    sv1, sv2, _ = st.columns([1.2, 1.8, 1])
                    with sv1:
                        save_now = form_submit_button(
                            "💾 Salvar alterações",
                            key=f"save_{kb}",
                            help="Valida e prepara as alterações feitas no grid deste setor antes da confirmação final.",
                        )
                    with sv2:
                        st.caption(
                            "Marque/desmarque etapas acima e clique em Salvar.")

                    _pending_changes_key = f"pending_changes_{kb}"
                    _pending_preview_key = f"pending_preview_{kb}"
                    _field_lbl = {
                        "etapa_d": "D",
                        "etapa_r": "R",
                        "etapa_m": "M"}

                    if save_now:
                        if edited is None:
                            st.warning(
                                "Troque para o modo **Editar** para poder salvar alterações.")
                        else:
                            changes = []
                            for equip_id, row in edited.iterrows():
                                if equip_id not in df_display.index:
                                    continue
                                for col in svc_bool:
                                    ov = bool(df_display.loc[equip_id, col])
                                    nv = bool(row[col])
                                    if ov != nv:
                                        sid, field = col_meta[col]
                                        changes.append((equip_id, sid, field, nv))
                            if not changes:
                                st.session_state.pop(
                                    _pending_changes_key, None)
                                st.session_state.pop(
                                    _pending_preview_key, None)
                                st.info(
                                    "Nenhuma alteração detectada — faça alguma marcação antes de salvar.")
                            else:
                                _prev_lines = build_change_preview_lines(
                                    changes,
                                    eq_label_short=eq_label_short,
                                    svc_names=_svc_names,
                                    field_labels=_field_lbl,
                                    limit=8,
                                )
                                st.session_state[_pending_changes_key] = changes
                                st.session_state[_pending_preview_key] = _prev_lines
                                st.rerun()

                    pending_changes = st.session_state.get(
                        _pending_changes_key) or []
                    pending_preview = st.session_state.get(
                        _pending_preview_key) or []
                    if pending_changes:
                        with st.container(border=True):
                            st.markdown(
                                f"**{len(pending_changes)} alteração(ões) a salvar:**")
                            st.markdown("\n".join(pending_preview))
                            c_yes, c_no, _ = st.columns([1, 1, 2])
                            with c_yes:
                                confirm_now = st.button(
                                    "✅ Confirmar", key=f"yes_{kb}", type="primary", use_container_width=True)
                            with c_no:
                                cancel_now = st.button(
                                    "✖ Cancelar", key=f"no_{kb}", use_container_width=True)

                        if cancel_now:
                            st.session_state.pop(_pending_changes_key, None)
                            st.session_state.pop(_pending_preview_key, None)
                            st.rerun()

                        if confirm_now:
                            now_iso = datetime.now(timezone.utc).isoformat()
                            missing = 0
                            payload_updates = []

                            for eid, sid, field, nv in pending_changes:
                                t = task_map.get((eid, sid)) or {}
                                tid = t.get("id")
                                if not tid:
                                    missing += 1
                                    continue

                                upd = {
                                    "id": tid,
                                    field: bool(nv),
                                    "updated_by": current_user_id() or None,
                                }
                                dtf = {
                                    "etapa_d": "dt_etapa_d",
                                    "etapa_r": "dt_etapa_r",
                                    "etapa_m": "dt_etapa_m",
                                }.get(field)
                                if dtf:
                                    upd[dtf] = now_iso if nv else None
                                if nv and not t.get("semana") and int(semana_lote) > 0:
                                    upd["semana"] = int(semana_lote)

                                payload_updates.append(upd)

                            pb = st.empty()
                            with st.spinner(f"Aplicando {len(payload_updates)} alterações em lote..."):
                                ok, failed = _bulk_update_tasks(sb, payload_updates)

                            st.session_state.pop(_pending_changes_key, None)
                            st.session_state.pop(_pending_preview_key, None)
                            pb.success(
                                f"✅ {ok} etapas salvas"
                                + (f"  ·  {failed} falharam" if failed else "")
                                + (f"  ·  {missing} não encontradas" if missing else "")
                            )
                            st.toast("✅ Alterações aplicadas com sucesso!")
                            bump_data_version()
                            try:
                                _load_payload.clear()
                            except Exception:
                                pass
                            try:
                                _group_kpis.clear()
                            except Exception:
                                pass
                            try:
                                nav.rerun_keep_menu()
                            except Exception:
                                st.rerun()

                    exp_df = df_display.reset_index(drop=True).copy()
                    for c in [
                        c for c in exp_df.columns if c not in (
                            "%", "Equipamento", "Status")]:
                        exp_df[c] = exp_df[c].apply(
                            lambda v: "OK" if bool(v) else "")
                    # sector_tables_for_export já foi pré-populado antes das
                    # tabs (fix #3)
                    st.markdown("</div>", unsafe_allow_html=True)

        # ── TAB: EVOLUÇÃO SEMANAL ──
        with tab_evolucao:
            st.markdown("### Evolução semanal")
            st.caption(
                "Acompanhe o ritmo de conclusão semana a semana versus a meta linear.")

            col_evo1, col_evo2 = st.columns([1, 2])
            with col_evo1:
                rank_mode = st.radio("Escopo:",
                                     ["Grupo inteiro",
                                      "Setor específico"],
                                     horizontal=False,
                                     key=f"evo_mode_{revisao_id}_{grupo_id}")
            setor_sel_rank = None
            with col_evo2:
                if rank_mode == "Setor específico":
                    setores_rank = sorted(
                        setor_to_services.keys(), key=lambda x: x.lower())
                    # FIX #7: persistir setor selecionado entre reruns
                    _evo_setor_key = f"evo_setor_val_{grupo_id}"
                    _evo_default = st.session_state.get(
                        _evo_setor_key, setores_rank[0] if setores_rank else None)
                    _evo_idx = setores_rank.index(
                        _evo_default) if _evo_default in setores_rank else 0
                    setor_sel_rank = st.selectbox(
                        "Setor",
                        setores_rank,
                        index=_evo_idx,
                        key=f"evo_setor_{revisao_id}_{grupo_id}")
                    st.session_state[_evo_setor_key] = setor_sel_rank
                else:
                    st.caption(
                        f"Analisando **{
                            len(eqs)} equipamentos** · **{
                            len(all_services)} serviços** · **{
                            len(all_services) *
                            3 *
                            len(eqs)} etapas** no total")

            chosen = all_services if rank_mode == "Grupo inteiro" else sorted(
                setor_to_services.get(
                    setor_sel_rank, []), key=lambda x: (
                    x.get("nome") or "").lower())
            seen_e = set()
            svc_ids_rank = []
            for s in chosen:
                sid = s.get("id")
                if sid and sid not in seen_e:
                    seen_e.add(sid)
                    svc_ids_rank.append(sid)
            total_cells_rank = int(len(eqs) * max(len(svc_ids_rank), 1) * 3)
            rev_start2 = pd.to_datetime((rev_row or {}).get("data_inicio") or (
                rev_row or {}).get("created_at"), errors="coerce", utc=True)
            if pd.isna(rev_start2):
                rev_start2 = pd.Timestamp(_now_utc()).normalize()
            df_tasks = pd.DataFrame(tarefas)
            if not df_tasks.empty:
                has_dt = any(
                    (c in df_tasks.columns and df_tasks[c].notna().any()) for c in [
                        "dt_etapa_d", "dt_etapa_r", "dt_etapa_m"])
                if has_dt:
                    def _wk(s):
                        dt = pd.to_datetime(s, errors="coerce", utc=True)
                        return ((dt - rev_start2).dt.days.clip(lower=0) //
                                7 + 1).astype("Int64")
                    events = []
                    for dc in ["dt_etapa_d", "dt_etapa_r", "dt_etapa_m"]:
                        if dc not in df_tasks.columns:
                            continue
                        sub = df_tasks[df_tasks["servico_id"].isin(
                            svc_ids_rank)].copy()
                        if sub.empty:
                            continue
                        sub["wk"] = _wk(sub[dc])
                        sub = sub.dropna(subset=["wk"])
                        if not sub.empty:
                            events.append(sub[["wk"]].assign(cnt=1))
                    if events:
                        ev = pd.concat(events, ignore_index=True)
                        agg = ev.groupby("wk", dropna=True)[
                            "cnt"].sum().sort_index()
                        cum = agg.cumsum()
                        mw = int(max(cum.index.max(), agg.index.max()))
                        idx = range(1, mw + 1)
                        pc = (cum / max(total_cells_rank, 1) *
                              100).round(1).to_frame("Cumulativo (%)")
                        ps = (agg / max(total_cells_rank, 1) *
                              100).round(1).to_frame("Na semana (%)")
                        pc = pc.reindex(idx).ffill().fillna(0)
                        ps = ps.reindex(idx).fillna(0)
                        wt = int(
                            (rev_row or {}).get("semanas_total") or mw or 1)
                        meta = pd.Series([min(100.0, (w / max(wt, 1)) * 100)
                                         for w in idx], index=idx, name="Meta (%)")
                        # KPIs de evolução
                        pct_atual = float(
                            pc["Cumulativo (%)"].iloc[-1]) if not pc.empty else 0
                        sem_atual = int(pc.index[-1]) if not pc.empty else 0
                        meta_atual = float(
                            meta.iloc[-1]) if len(meta) > 0 else 0
                        delta_vs_meta = round(pct_atual - meta_atual, 1)
                        mk1, mk2, mk3, mk4 = st.columns(4)
                        mk1.metric("Progresso atual", f"{pct_atual:.1f}%")
                        mk2.metric("Meta (semana atual)",
                                   f"{meta_atual:.1f}%",
                                   delta=f"{delta_vs_meta:+.1f}%",
                                   delta_color="normal" if delta_vs_meta >= 0 else "inverse")
                        mk3.metric("Semanas decorridas", str(sem_atual))
                        mk4.metric("Total etapas",
                                   f"{int(cum.iloc[-1])}/{total_cells_rank}")
                        st.divider()
                        st.line_chart(pc.join(ps).join(meta))
                        with st.expander("📋 Tabela detalhada", expanded=False):
                            det = pc.join(ps).join(meta).copy()
                            det["Concluídos (semana)"] = agg.reindex(
                                idx).fillna(0).astype(int).values
                            det["Concluídos (acum.)"] = cum.reindex(
                                idx).ffill().fillna(0).astype(int).values
                            st.dataframe(
                                det.reset_index(
                                    names="Semana"),
                                use_container_width=True,
                                hide_index=True)
                    else:
                        st.info(
                            "Ainda não há timestamps suficientes para gerar o gráfico.")
                elif "semana" in df_tasks.columns:
                    df_done = df_tasks[(df_tasks["servico_id"].isin(
                        svc_ids_rank)) & df_tasks["semana"].notna()].copy()
                    if not df_done.empty:
                        df_done["semana"] = pd.to_numeric(
                            df_done["semana"], errors="coerce").astype("Int64")
                        df_done = df_done.dropna(subset=["semana"])
                        cum_vals = []
                        for w in sorted(df_done["semana"].unique()):
                            w_df = df_done[df_done["semana"] <= w]
                            ok_w = int(w_df[["etapa_d", "etapa_r", "etapa_m"]].fillna(
                                False).astype(bool).astype(int).sum().sum())
                            cum_vals.append({"Semana": int(w), "% Concluído": round(
                                (ok_w / max(total_cells_rank, 1)) * 100, 1)})
                        st.line_chart(
                            pd.DataFrame(cum_vals).set_index("Semana"))
                    else:
                        st.info("Sem dados de evolução.")
                else:
                    st.info("Sem timestamps nem coluna semana disponíveis.")
            else:
                st.info("Sem tarefas para esta revisão/grupo.")


        # ── TAB: ANALYTICS & AUTOMAÇÃO ──
        with tab_analytics:
            st.markdown("### Gestão e automação")
            st.caption("Indicadores executivos, riscos do grupo e atalhos operacionais seguros.")

            a1, a2, a3, a4 = st.columns(4)
            a1.metric("Progresso geral", f"{progresso_atual_pct:.0f}%")
            a2.metric(
                "Meta esperada",
                f"{expected_pct_now:.1f}%",
                delta=f"{delta_vs_expected_now:+.1f}%",
                delta_color="normal" if delta_vs_expected_now >= 0 else "inverse",
            )
            a3.metric("Setores risco alto", sum(1 for item in analytics_sector_intelligence if item.get("risk") == "alto"))
            a4.metric("Equip. críticos", critical_eq_count)

            for insight in automation_insights[:5]:
                level = str(insight.get("nivel") or "info")
                title = str(insight.get("titulo") or "")
                body = str(insight.get("texto") or "")
                if level == "error":
                    st.error(f"**{title}** — {body}")
                elif level == "warning":
                    st.warning(f"**{title}** — {body}")
                elif level == "success":
                    st.success(f"**{title}** — {body}")
                else:
                    st.info(f"**{title}** — {body}")

            b1, b2, b3 = st.columns(3)
            with b1:
                if st.button("Abrir setores críticos", key=f"mtz_auto_open_high_{grupo_id}", use_container_width=True):
                    opened = 0
                    for item in analytics_priority_sorted:
                        if item.get("risk") == "alto":
                            _sector_set_open(revisao_id, grupo_id, str(item.get("setor_nome")), True)
                            opened += 1
                    if opened:
                        st.toast(f"{opened} setor(es) críticos preparados na aba Matriz.")
                    else:
                        st.toast("Nenhum setor crítico para abrir.")
                    st.rerun()
            with b2:
                if st.button("Abrir top 3 prioridades", key=f"mtz_auto_open_top3_{grupo_id}", use_container_width=True):
                    opened = 0
                    for item in analytics_priority_sorted[:3]:
                        _sector_set_open(revisao_id, grupo_id, str(item.get("setor_nome")), True)
                        opened += 1
                    if opened:
                        st.toast(f"Top {opened} prioridades preparadas na aba Matriz.")
                    st.rerun()
            with b3:
                if st.button("Fechar setores sob controle", key=f"mtz_auto_close_low_{grupo_id}", use_container_width=True):
                    closed = 0
                    for item in analytics_sector_intelligence:
                        if item.get("risk") == "baixo":
                            _sector_set_open(revisao_id, grupo_id, str(item.get("setor_nome")), False)
                            closed += 1
                    if closed:
                        st.toast(f"{closed} setor(es) sob controle fechados.")
                    st.rerun()
            st.markdown("#### Equipamentos que exigem atenção")
            if resumo_df.empty:
                st.info("Sem dados de equipamentos para análise.")
            else:
                critical_equipment_df = resumo_df.copy()
                critical_equipment_df["Risco"] = critical_equipment_df["%"].apply(
                    lambda v: "alto" if int(v) < 50 else ("medio" if int(v) < 80 else "baixo")
                )
                critical_equipment_df = critical_equipment_df.sort_values(
                    by=["%", "Concluidos"],
                    ascending=[True, True],
                ).head(10)[["Equipamento", "%", "Concluidos", "Total", "Risco"]]
                st.dataframe(
                    critical_equipment_df,
                    use_container_width=True,
                    hide_index=True,
                )

            st.markdown("#### Lead time médio entre etapas")
            if view_agg.empty:
                st.info("Sem dados de tempo suficientes para calcular lead time.")
            else:
                lt_dr = pd.to_numeric(view_agg.get("D→R (h)"), errors="coerce")
                lt_rm = pd.to_numeric(view_agg.get("R→M (h)"), errors="coerce")
                lt_dm = pd.to_numeric(view_agg.get("D→M (h)"), errors="coerce")
                l1, l2, l3 = st.columns(3)
                l1.metric("Mediana D→R", _fmt_duration_from_hours(lt_dr.dropna().median() if lt_dr is not None and not lt_dr.dropna().empty else None))
                l2.metric("Mediana R→M", _fmt_duration_from_hours(lt_rm.dropna().median() if lt_rm is not None and not lt_rm.dropna().empty else None))
                l3.metric("Mediana D→M", _fmt_duration_from_hours(lt_dm.dropna().median() if lt_dm is not None and not lt_dm.dropna().empty else None))

        # ── TAB: TEMPOS ──
        with tab_tempos:
            st.markdown("### ⏱️ Tempos de execução (D/R/M)")
            st.caption(
                "Análise de duração entre as etapas Desmontagem → Revisão → Montagem.")
            svc_ids_tempos = svc_ids_rank if svc_ids_rank else svc_ids_all
            tempos_rows = []
            try:
                tempos_rows = (
                    sb.table("v_tarefas_etapas_duracoes") .select(
                        "equipamento_id,servico_id,dt_inicio,dt_etapa_d,dt_etapa_r,dt_etapa_m,"
                        "horas_d_para_r,horas_r_para_m,horas_d_para_m,horas_total") .eq(
                        "tenant_id",
                        tenant_id).eq(
                        "revisao_id",
                        revisao_id) .in_(
                        "equipamento_id",
                        eq_ids).execute().data) or []
            except Exception:
                tempos_rows = []
            df_t = pd.DataFrame(
                tempos_rows) if tempos_rows else pd.DataFrame(tarefas)
            if not tempos_rows:
                for col in [
                    "dt_inicio",
                    "dt_etapa_d",
                    "dt_etapa_r",
                        "dt_etapa_m"]:
                    if col not in df_t.columns:
                        df_t[col] = pd.NaT
                    df_t[col] = pd.to_datetime(
                        df_t[col], errors="coerce", utc=True)
                df_t["horas_d_para_r"] = (
                    df_t["dt_etapa_r"] - df_t["dt_etapa_d"]).dt.total_seconds() / 3600
                df_t["horas_r_para_m"] = (
                    df_t["dt_etapa_m"] - df_t["dt_etapa_r"]).dt.total_seconds() / 3600
                df_t["horas_d_para_m"] = (
                    df_t["dt_etapa_m"] - df_t["dt_etapa_d"]).dt.total_seconds() / 3600
                df_t["horas_total"] = (
                    df_t["dt_etapa_m"] - df_t["dt_inicio"]).dt.total_seconds() / 3600
            if "servico_id" in df_t.columns:
                df_t = df_t[df_t["servico_id"].isin(svc_ids_tempos)].copy()
            view_agg = pd.DataFrame()
            if not df_t.empty:
                sv_map = {s["id"]: (s.get("nome") or str(s["id"]))
                          for s in all_services if s.get("id")}
                # usar rótulo curto na tabela de tempos
                df_t["Frota"] = df_t["equipamento_id"].map(eq_label_short)
                df_t["Equipamento"] = df_t["equipamento_id"].map(
                    eq_label)  # mantido para export
                df_t["Serviço"] = df_t["servico_id"].map(
                    sv_map).fillna(df_t["servico_id"].astype(str))
                for c in [
                    "horas_d_para_r",
                    "horas_r_para_m",
                    "horas_d_para_m",
                        "horas_total"]:
                    if c in df_t.columns:
                        df_t[c] = pd.to_numeric(df_t[c], errors="coerce")

                # KPIs globais de tempo
                med_total = df_t["horas_total"].dropna().mean(
                ) if "horas_total" in df_t.columns else None
                med_dr = df_t["horas_d_para_r"].dropna().mean(
                ) if "horas_d_para_r" in df_t.columns else None
                med_rm = df_t["horas_r_para_m"].dropna().mean(
                ) if "horas_r_para_m" in df_t.columns else None
                completos_total = int(
                    df_t["horas_total"].notna().sum()) if "horas_total" in df_t.columns else 0
                tk1, tk2, tk3, tk4 = st.columns(4)
                tk1.metric("Itens completos", str(completos_total))
                tk2.metric(
                    "Média total (D→M)",
                    _fmt_duration_from_hours(med_total))
                tk3.metric("Média D→R", _fmt_duration_from_hours(med_dr))
                tk4.metric("Média R→M", _fmt_duration_from_hours(med_rm))
                st.divider()

                t_col1, t_col2 = st.columns([1, 1])
                with t_col1:
                    st.markdown("#### Resumo por frota")
                    agg = (
                        df_t.groupby(
                            "Frota", dropna=False) .agg(
                            itens=(
                                "servico_id", "count"), completos=(
                                "horas_total", lambda s: int(
                                    pd.Series(s).notna().sum())), media_total_h=(
                                "horas_total", "mean"), p90_total_h=(
                                "horas_total", lambda s: float(
                                    pd.Series(s).dropna().quantile(.9)) if pd.Series(s).dropna().shape[0] else None), media_d_r_h=(
                                        "horas_d_para_r", "mean"), media_r_m_h=(
                                            "horas_r_para_m", "mean")) .reset_index())
                    agg["Média Total"] = agg["media_total_h"].apply(
                        _fmt_duration_from_hours)
                    agg["P90"] = agg["p90_total_h"].apply(
                        _fmt_duration_from_hours)
                    agg["D→R"] = agg["media_d_r_h"].apply(
                        _fmt_duration_from_hours)
                    agg["R→M"] = agg["media_r_m_h"].apply(
                        _fmt_duration_from_hours)
                    view_agg_short = agg[["Frota",
                                          "itens",
                                          "completos",
                                          "Média Total",
                                          "P90",
                                          "D→R",
                                          "R→M"]].sort_values(["completos",
                                                               "itens"],
                                                              ascending=[False,
                                                                         False])
                    # view_agg para export ainda usa Equipamento
                    agg2 = agg.copy()
                    agg2["Equipamento"] = agg2["Frota"].map(
                        {v: eq_label.get(k, v) for k, v in eq_label_short.items()})
                    view_agg = agg2[["Equipamento",
                                     "itens",
                                     "completos",
                                     "Média Total",
                                     "P90",
                                     "D→R",
                                     "R→M"]].sort_values(["completos",
                                                          "itens"],
                                                         ascending=[False,
                                                                    False])
                    st.dataframe(view_agg_short.style .set_properties(subset=["Frota"],
                                                                      **{"text-align": "left",
                                                                         "font-weight": "600"}) .set_properties(**{"font-size": "12px"}),
                                 use_container_width=True,
                                 hide_index=True)

                with t_col2:
                    st.markdown("#### Gargalos — Top tempos")
                    metric = st.selectbox(
                        "Ordenar por:", [
                            "Total (D→M)", "D→R", "R→M"], index=0, key="tempo_metric")
                    col_m = {
                        "Total (D→M)": "horas_total",
                        "D→R": "horas_d_para_r",
                        "R→M": "horas_r_para_m"}[metric]
                    top = df_t[["Frota", "Serviço", "horas_d_para_r",
                                "horas_r_para_m", "horas_total"]].copy()
                    top = top.dropna(
                        subset=[col_m]).sort_values(
                        by=[col_m],
                        ascending=False).head(20)
                    top["D→R"] = top["horas_d_para_r"].apply(
                        _fmt_duration_from_hours)
                    top["R→M"] = top["horas_r_para_m"].apply(
                        _fmt_duration_from_hours)
                    top["Total"] = top["horas_total"].apply(
                        _fmt_duration_from_hours)
                    st.dataframe(top[["Frota",
                                      "Serviço",
                                      "D→R",
                                      "R→M",
                                      "Total"]] .style.set_properties(subset=["Frota",
                                                                              "Serviço"],
                                                                      **{"text-align": "left"}) .set_properties(**{"font-size": "12px"}),
                                 use_container_width=True,
                                 hide_index=True)
            else:
                st.info(
                    "Sem dados de tempo ainda. Marque etapas D/R/M com timestamps para começar.")

        # ── TAB: EDITAR CÉLULA ──
        with tab_editor:
            st.markdown("### ✏️ Edição rápida por célula")
            st.caption(
                "Selecione frota, setor e serviço para atualizar etapas, status e observação.")

            # Seletores lado a lado
            ed_c1, ed_c2, ed_c3 = st.columns([1, 1, 1])
            with ed_c1:
                equip_choices_short = {
                    eq_label_short[eid]: eid for eid in eq_label_short}
                esl = st.selectbox(
                    "🚜 Frota",
                    list(
                        equip_choices_short.keys()),
                    key="mat_eq_sel")
                equip_sel = equip_choices_short[esl]
            with ed_c2:
                setores_ed = sorted(
                    setor_to_services.keys(),
                    key=lambda x: x.lower())
                if setores_ed:
                    setor_ed = st.selectbox(
                        "📂 Setor", setores_ed, key="mat_setor_sel")
                else:
                    st.info("Sem setores disponíveis neste grupo.")
                    setor_ed = None
            with ed_c3:
                if setor_ed:
                    svs_ed = sorted(
                        setor_to_services[setor_ed], key=lambda x: (
                            x.get("nome") or "").lower())
                    svc_choices = {
                        s.get("nome") or str(
                            s.get("id")): s["id"] for s in svs_ed if s.get("id")}
                    if svc_choices:
                        svc_name = st.selectbox("🔧 Serviço", list(
                            svc_choices.keys()), key="mat_srv_sel")
                        svc_sel = svc_choices[svc_name]
                    else:
                        st.info("Sem serviços neste setor.")
                        svc_sel = None
                else:
                    svc_sel = None

            if not setor_ed or not svc_sel:
                st.info("Selecione um setor e serviço válidos para continuar.")
            else:
                # Buscar tarefa
                task_rows_ed = (
                    sb.table("tarefas_servico").select("id,status,semana,observacao,etapa_d,etapa_r,etapa_m") .eq(
                        "tenant_id", tenant_id).eq(
                        "revisao_id", revisao_id) .eq(
                        "equipamento_id", equip_sel).eq(
                        "servico_id", svc_sel).limit(1).execute().data) or []
                task_ed = task_rows_ed[0] if task_rows_ed else None

            if not task_ed:
                st.warning("⚠️ Tarefa não encontrada para esta combinação.")
            else:
                st.divider()
                # Info da tarefa atual em destaque
                cur_d = bool(task_ed.get("etapa_d"))
                cur_r = bool(task_ed.get("etapa_r"))
                cur_m = bool(task_ed.get("etapa_m"))
                cur_pct = round(
                    ((int(cur_d) + int(cur_r) + int(cur_m)) / 3) * 100)
                _ed_color = _risk_color(cur_pct)

                def _badge(label, done):
                    if done:
                        return (f'<span style="padding:3px 10px;border-radius:999px;'
                                f'background:rgba(18,183,106,.2);color:#12B76A;font-size:.8rem">✓ {label}</span>')
                    return (f'<span style="padding:3px 10px;border-radius:999px;'
                            f'background:rgba(255,255,255,.06);color:rgba(255,255,255,.4);font-size:.8rem">✗ {label}</span>')

                badge_d = _badge("D", cur_d)
                badge_r = _badge("R", cur_r)
                badge_m = _badge("M", cur_m)
                _status_label = "Concluído" if cur_pct == 100 else (
                    "Pendente" if cur_pct == 0 else "Em andamento")

                info_col1, info_col2 = st.columns([2, 1])
                with info_col1:
                    st.markdown(
                        f'<div style="padding:10px 14px;border-radius:10px;border:1px solid rgba(255,255,255,.1);'
                        f'background:rgba(255,255,255,.04);margin-bottom:8px">'
                        f'<div style="font-size:.8rem;color:rgba(255,255,255,.5);margin-bottom:4px">Estado atual</div>'
                        f'<div style="display:flex;gap:12px;align-items:center">'
                        f'<span style="font-size:.9rem">Frota <b>{esl}</b></span>'
                        f'<span style="color:rgba(255,255,255,.4)">·</span>'
                        f'<span style="font-size:.9rem">{setor_ed}</span>'
                        f'<span style="color:rgba(255,255,255,.4)">·</span>'
                        f'<span style="font-size:.9rem">{svc_name}</span>'
                        f'</div>'
                        f'<div style="margin-top:6px;display:flex;gap:6px">'
                        f'{badge_d}{badge_r}{badge_m}'
                        f'</div></div>',
                        unsafe_allow_html=True)
                with info_col2:
                    st.metric(
                        "Progresso atual",
                        f"{cur_pct}%",
                        delta=_status_label)

                st.markdown("#### Atualizar etapas")
                cD, cR, cM, cSem = st.columns([1, 1, 1, 1])
                with cD:
                    etapa_d = st.checkbox(
                        "✅ Desmontou (D)", value=cur_d, key="mat_ed_d")
                with cR:
                    etapa_r = st.checkbox(
                        "✅ Revisou (R)", value=cur_r, key="mat_ed_r")
                with cM:
                    etapa_m = st.checkbox(
                        "✅ Montou (M)", value=cur_m, key="mat_ed_m")
                with cSem:
                    _semana_ed_default = int(
                        task_ed.get("semana") or _semana_sugerida)
                    nsem = st.number_input("📅 Semana", min_value=0,
                                           value=_semana_ed_default, step=1, key="mat_sem",
                                           help=f"Semana sugerida automaticamente: {_semana_sugerida}. "
                                           "Altere se precisar registrar em outra semana.")

                st.caption(
                    "Marcar D+R+M atualiza o status para Concluído automaticamente.")

                SO = [
                    ("pendente",
                     "⏳ Pendente"),
                    ("em_andamento",
                     "🔄 Em andamento"),
                    ("concluido",
                     "✅ Concluído"),
                    ("travado",
                     "🚫 Travado"),
                    ("nao_aplica",
                     "➖ Não aplica")]
                kl = [k for k, _ in SO]
                ll = [v for _, v in SO]
                ist = kl.index(task_ed["status"]) if task_ed.get(
                    "status") in kl else 0
                st_col1, st_col2 = st.columns([1, 2])
                with st_col1:
                    nlbl = st.selectbox(
                        "📌 Status", ll, index=ist, key="mat_st_sel")
                    nst = kl[ll.index(nlbl)]
                with st_col2:
                    nobs = st.text_area(
                        "💬 Observação",
                        value=task_ed.get("observacao") or "",
                        key="mat_obs_ed",
                        height=80,
                        placeholder="Descreva impedimentos, peças aguardadas, ocorrências...")

                sv_a, sv_b, _ = st.columns([1, 1, 2])
                with sv_a:
                    save_quick = form_submit_button(
                        "💾 Salvar",
                        key="mat_save_ed",
                        help="Salva as etapas, semana, status e observação da tarefa selecionada.",
                    )
                    if save_quick:
                        new_status = nst
                        if etapa_d and etapa_r and etapa_m:
                            new_status = "concluido"

                        quick_errors = []
                        if new_status == "travado" and not (nobs or "").strip():
                            quick_errors.append("Preencha a observação antes de salvar uma tarefa como Travado.")

                        if quick_errors:
                            validation_summary(quick_errors, title="Corrija o formulário da tarefa")
                        else:
                            try:
                                sb.table("tarefas_servico").update({
                                    "etapa_d": bool(etapa_d), "etapa_r": bool(etapa_r), "etapa_m": bool(etapa_m),
                                    "status": new_status, "semana": int(nsem) if int(nsem) > 0 else None,
                                    "observacao": nobs.strip() or None, "updated_by": current_user_id() or None
                                }).eq("id", task_ed["id"]).execute()
                                st.success(
                                    f"✅ Frota {esl} · {svc_name} atualizado!")
                                bump_data_version()
                                try:
                                    nav.rerun_keep_menu()
                                except Exception:
                                    st.rerun()
                            except Exception as e:
                                st.error(f"Erro: {e}")
                with sv_b:
                    # Limpar observação rapidamente
                    if (task_ed.get("observacao") or "").strip():
                        if st.button(
                            "🗑️ Limpar obs.",
                            use_container_width=True,
                            key="mat_clear_obs",
                        ):
                            st.session_state["confirm_clear_obs_matriz"] = True
                            st.rerun()

                        if confirmation_panel(
                            state_key="confirm_clear_obs_matriz",
                            title="Confirma limpar a observação desta tarefa?",
                            body="A observação atual será removida imediatamente da tarefa selecionada.",
                            confirm_label="Limpar observação",
                        ):
                            try:
                                sb.table("tarefas_servico").update(
                                    {"observacao": None}).eq("id", task_ed["id"]).execute()
                                st.toast("Observação removida.")
                                bump_data_version()
                                try:
                                    nav.rerun_keep_menu()
                                except Exception:
                                    st.rerun()
                            except Exception as e:
                                st.error(f"Erro: {e}")

                # ── Histórico de comentários ─────────────────────────────────
                st.markdown("---")
                try:
                    from src.ui.components.comentarios import render_comentarios
                    _u_id = current_user_id() or ""
                    _u_nome = st.session_state.get("sb_user_nome") or "Usuário"
                    render_comentarios(
                        tenant_id, task_ed["id"],
                        user_nome=_u_nome,
                        key_prefix=f"mtz_{equip_sel}_{svc_sel}_",
                    )
                except Exception:
                    pass  # comentários são opcionais — tabela pode não existir ainda

        # ── TAB: EXPORTAR ──
        with tab_exportar:
            st.markdown("### Exportações")
            res_exp = resumo_df if (
                isinstance(
                    resumo_df,
                    pd.DataFrame) and not resumo_df.empty) else pd.DataFrame()
            va_exp = view_agg if (
                isinstance(
                    view_agg,
                    pd.DataFrame) and not view_agg.empty) else pd.DataFrame()

            # FIX #13: mostrar contexto (nº linhas) antes dos botões
            _n_res = len(res_exp) if not res_exp.empty else 0
            _n_va = len(va_exp) if not va_exp.empty else 0
            _n_set = len(sector_tables_for_export)

            c1e, c2e = st.columns(2)
            with c1e:
                st.caption(f"📋 Resumo por equipamento — {_n_res} linha(s)")
                _res_sorted = res_exp.sort_values(
                    by=[c for c in ["Score", "%", "Equipamento"] if c in res_exp.columns],
                    ascending=[False, True, True][:sum(1 for c in ["Score", "%", "Equipamento"] if c in res_exp.columns)]
                ) if not res_exp.empty else res_exp
                st.download_button(
                    "⬇️ Baixar resumo (CSV)",
                    data=_df_to_csv_bytes(_res_sorted) if not res_exp.empty else b"",
                    file_name=f"resumo_{grupo_nome}.csv".replace(
                        "/",
                        "-"),
                    mime="text/csv",
                    use_container_width=True,
                    disabled=res_exp.empty)
            with c2e:
                _va_label = "por tarefa" if (
                    "Serviço" in va_exp.columns and not va_exp.empty) else ""
                st.caption(
                    f"⏱️ Tempos de execução {_va_label} — {_n_va} linha(s)")
                st.download_button(
                    "⬇️ Baixar tempos (CSV)",
                    data=_df_to_csv_bytes(va_exp) if not va_exp.empty else b"",
                    file_name=f"tempos_{grupo_nome}.csv".replace(
                        "/",
                        "-"),
                    mime="text/csv",
                    use_container_width=True,
                    disabled=va_exp.empty)

            st.divider()
            st.markdown("#### PDF completo")
            # FIX #3: sector_tables já pré-populado — PDF sempre disponível ao
            # abrir a aba
            if _n_set == 0:
                st.warning(
                    "Nenhum dado de setor disponível para gerar o PDF. Verifique se há equipamentos e template configurados.")
            elif not _reportlab_available():
                st.info(
                    "Instale `reportlab` no requirements.txt para habilitar a exportação em PDF.")
            else:
                st.caption(
                    f"Relatório com {_n_set} setor(es) · {_n_res} equipamento(s)")

                # Evita reaproveitar bytes do grupo/revisão anterior no
                # download.
                export_signature = (
                    str(tenant_id),
                    str(grupo_id),
                    str(revisao_id),
                    str(st.session_state.get("data_version", "0")),
                    int(_n_res),
                    int(_n_set),
                )
                prev_signature = st.session_state.get(
                    "mtz_pdf_export_signature")
                if prev_signature != export_signature:
                    st.session_state.pop("mtz_pdf_export_bytes", None)
                    st.session_state["mtz_pdf_export_signature"] = export_signature

                if "mtz_pdf_export_bytes" not in st.session_state:
                    resumo_pdf_df = resumo_df.copy() if isinstance(
                        resumo_df, pd.DataFrame) else pd.DataFrame()
                    sector_tables_pdf = [
                        (setor_nome, setor_df.copy())
                        for setor_nome, setor_df in (sector_tables_for_export or [])
                    ]
                    st.session_state["mtz_pdf_export_bytes"] = _build_pdf_tables(
                        titulo=titulo,
                        grupo_nome=grupo_nome,
                        resumo_df=resumo_pdf_df,
                        sector_tables=sector_tables_pdf,
                    )

                pdf_bytes = st.session_state["mtz_pdf_export_bytes"]
                pdf_file_name = f"relatorio_matriz_{grupo_nome}.pdf".replace(
                    "/", "-")
                st.download_button(
                    "⬇️ Baixar PDF completo",
                    data=pdf_bytes,
                    file_name=pdf_file_name,
                    mime="application/pdf",
                    use_container_width=True,
                    type="primary",
                    key=f"mtz_pdf_download_{grupo_id}_{revisao_id}_{_n_res}_{_n_set}",
                )

    except Exception as e:
        st.error("Erro ao renderizar a Matriz.")
        st.exception(e)
