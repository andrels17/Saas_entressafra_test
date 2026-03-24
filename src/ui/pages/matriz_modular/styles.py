from __future__ import annotations

import streamlit as st

from src.ui.pages.matriz_runtime import risk_color as _risk_color

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
.mtz-card-grid [data-testid="stButton"]{margin-bottom:10px}
.mtz-card-grid{margin-top:10px}
.mtz-select-card{
  border-radius:18px;
  padding:4px 4px 2px 4px;
  background:linear-gradient(180deg, rgba(255,255,255,.03), rgba(255,255,255,.01));
}
.mtz-select-card.high{
  box-shadow:0 0 0 1px rgba(239,68,68,.28) inset;
}
.mtz-select-card.medium{
  box-shadow:0 0 0 1px rgba(245,158,11,.24) inset;
}
.mtz-select-card.low{
  box-shadow:0 0 0 1px rgba(34,197,94,.20) inset;
}
.mtz-select-card.neutral{
  box-shadow:0 0 0 1px rgba(229,231,235,.14) inset;
}
.mtz-select-card .mtz-card-title{
  font-size:1rem;
  font-weight:700;
  line-height:1.15;
  color:#fff;
  margin-bottom:4px;
}
.mtz-select-card .mtz-card-subtitle{
  font-size:.83rem;
  line-height:1.1;
  color:rgba(255,255,255,.68);
  margin-bottom:10px;
}
.mtz-select-card .mtz-card-metrics{
  font-size:.88rem;
  font-weight:600;
  color:rgba(255,255,255,.94);
  margin-bottom:10px;
}
.mtz-select-card .mtz-card-status{
  display:inline-flex;
  align-items:center;
  gap:6px;
  font-size:.78rem;
  font-weight:700;
  color:rgba(255,255,255,.92);
  padding:4px 8px;
  border-radius:999px;
  background:rgba(255,255,255,.05);
  border:1px solid rgba(255,255,255,.08);
}
.mtz-select-card .mtz-card-status.critico{
  background:rgba(239,68,68,.12);
  border-color:rgba(239,68,68,.28);
}
.mtz-select-card .mtz-card-status.atencao{
  background:rgba(245,158,11,.12);
  border-color:rgba(245,158,11,.26);
}
.mtz-select-card .mtz-card-status.avancado{
  background:rgba(34,197,94,.12);
  border-color:rgba(34,197,94,.24);
}
.mtz-select-card .mtz-card-status.sem-dados{
  background:rgba(229,231,235,.08);
  border-color:rgba(229,231,235,.16);
}
.mtz-select-card .mtz-toolbar-actions [data-testid="stButton"] button{
  min-height:36px;
  padding:0 12px;
  border-radius:10px;
  border:1px solid rgba(255,255,255,.10);
  background:rgba(255,255,255,.03);
  color:rgba(255,255,255,.92);
  box-shadow:none;
  font-weight:600;
  font-size:.92rem;
}
.mtz-toolbar-actions [data-testid="stButton"] button:hover{
  border-color:rgba(255,255,255,.18);
  background:rgba(255,255,255,.06);
  transform:none;
  box-shadow:none;
}
.mtz-toolbar-actions .mtz-btn-primary [data-testid="stButton"] button{
  background:rgba(16,185,129,.10);
  border-color:rgba(16,185,129,.22);
}
.mtz-toolbar-actions .mtz-btn-primary [data-testid="stButton"] button:hover{
  background:rgba(16,185,129,.16);
  border-color:rgba(16,185,129,.30);
}

[data-testid="stButton"] button{
  width:100%;
  border-radius:12px;
  min-height:40px;
  font-weight:700;
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
.mtz-page-hero{margin:6px 0 10px 0;padding:2px 0 0 0}
.mtz-page-title{font-size:1.7rem;font-weight:800;letter-spacing:-.02em;margin:0 0 4px 0;color:#fff}
.mtz-page-sub{font-size:.90rem;color:rgba(255,255,255,.72);margin:0}

.mtz-quick-nav-head{padding:2px 0 4px 0}
.mtz-quick-nav-title{font-size:1.18rem;font-weight:800;letter-spacing:-.02em;color:#fff;line-height:1.1;margin:0}
.mtz-quick-nav-sub{font-size:.92rem;color:rgba(255,255,255,.70);margin-top:4px}
.mtz-focus-chip-wrap{display:flex;justify-content:flex-end;align-items:flex-start;height:100%}
.mtz-focus-chip{display:inline-flex;align-items:center;justify-content:center;min-height:38px;padding:0 14px;border-radius:999px;border:1px solid rgba(16,185,129,.24);background:linear-gradient(180deg, rgba(16,185,129,.10), rgba(16,185,129,.05));color:#ecfdf5;font-size:.88rem;font-weight:700;white-space:nowrap;box-shadow:inset 0 1px 0 rgba(255,255,255,.04)}
.mtz-toolbar-inline [data-testid="stButton"] button{min-height:40px;font-weight:700}
.mtz-toolbar-inline [data-testid="stNumberInput"] input{min-height:40px}
.mtz-toolbar-inline [data-testid="stTextInput"] input{min-height:40px}
.mtz-toolbar-inline [data-testid="stToggle"]{padding-top:8px}

/* Toolbar actions premium */
.mtz-inline-actions{
  border:1px solid rgba(255,255,255,.08);
  background:linear-gradient(180deg, rgba(255,255,255,.03), rgba(255,255,255,.012));
  border-radius:14px;
  padding:6px;
  box-shadow:0 10px 24px rgba(0,0,0,.16);
}
.mtz-inline-actions [data-testid="column"]{display:flex;align-items:stretch}
.mtz-inline-actions [data-testid="stButton"]{height:100%}
.mtz-inline-actions [data-testid="stButton"] button{
  min-height:42px !important;
  height:42px;
  padding:0 14px !important;
  border-radius:10px !important;
  font-size:.90rem !important;
  font-weight:700 !important;
  white-space:nowrap !important;
  letter-spacing:-.01em;
}
.mtz-inline-actions .mtz-btn-ghost [data-testid="stButton"] button{
  background:rgba(255,255,255,.018) !important;
  border:1px solid rgba(255,255,255,.09) !important;
  color:rgba(255,255,255,.84) !important;
}
.mtz-inline-actions .mtz-btn-ghost [data-testid="stButton"] button:hover{
  background:rgba(255,255,255,.04) !important;
  border-color:rgba(255,255,255,.16) !important;
  color:rgba(255,255,255,.96) !important;
}
.mtz-inline-actions .mtz-btn-neutral [data-testid="stButton"] button{
  background:rgba(16,185,129,.06) !important;
  border:1px solid rgba(16,185,129,.16) !important;
  color:rgba(255,255,255,.94) !important;
}
.mtz-inline-actions .mtz-btn-neutral [data-testid="stButton"] button:hover{
  background:rgba(16,185,129,.10) !important;
  border-color:rgba(16,185,129,.24) !important;
}
.mtz-inline-actions .mtz-btn-primary [data-testid="stButton"] button{
  background:linear-gradient(180deg, rgba(16,185,129,.18), rgba(16,185,129,.11)) !important;
  border:1px solid rgba(16,185,129,.28) !important;
  color:#f0fdf4 !important;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.05), 0 8px 18px rgba(16,185,129,.10) !important;
}
.mtz-inline-actions .mtz-btn-primary [data-testid="stButton"] button:hover{
  background:linear-gradient(180deg, rgba(16,185,129,.24), rgba(16,185,129,.14)) !important;
  border-color:rgba(16,185,129,.36) !important;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.06), 0 10px 22px rgba(16,185,129,.14) !important;
}
.mtz-inline-actions .mtz-btn-primary [data-testid="stButton"] button p,
.mtz-inline-actions .mtz-btn-neutral [data-testid="stButton"] button p,
.mtz-inline-actions .mtz-btn-ghost [data-testid="stButton"] button p{
  white-space:nowrap !important;
}
.mtz-inline-actions [data-testid="column"]{display:flex;align-items:stretch}
.mtz-inline-actions [data-testid="stButton"]{height:100%}
.mtz-inline-actions [data-testid="stButton"] > div{height:100%}
.mtz-inline-actions [data-testid="stButton"] button{
  min-height:46px;
  border-radius:12px;
  font-size:.92rem;
  font-weight:700;
  letter-spacing:.01em;
  border:1px solid rgba(255,255,255,.10);
  background:rgba(255,255,255,.035);
  color:rgba(255,255,255,.94);
  transition:all .18s ease;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.04);
}
.mtz-inline-actions [data-testid="stButton"] button:hover{
  transform:translateY(-1px);
  border-color:rgba(255,255,255,.18);
  background:rgba(255,255,255,.06);
  box-shadow:0 10px 18px rgba(0,0,0,.16);
}
.mtz-inline-actions [data-testid="stButton"] button:focus:not(:active){
  border-color:rgba(52,211,153,.38);
  box-shadow:0 0 0 1px rgba(52,211,153,.22), 0 10px 18px rgba(0,0,0,.16);
}
.mtz-btn-ghost [data-testid="stButton"] button{
  background:rgba(255,255,255,.018);
  color:rgba(255,255,255,.80);
}
.mtz-btn-ghost [data-testid="stButton"] button:hover{
  background:rgba(255,255,255,.045);
  color:rgba(255,255,255,.94);
}
.mtz-btn-neutral [data-testid="stButton"] button{
  background:linear-gradient(180deg, rgba(16,185,129,.08), rgba(255,255,255,.03));
  border-color:rgba(16,185,129,.16);
}
.mtz-btn-neutral [data-testid="stButton"] button:hover{
  border-color:rgba(16,185,129,.26);
  background:linear-gradient(180deg, rgba(16,185,129,.12), rgba(255,255,255,.05));
}
.mtz-btn-primary [data-testid="stButton"] button{
  background:linear-gradient(180deg, rgba(16,185,129,.28), rgba(5,150,105,.22));
  border-color:rgba(52,211,153,.34);
  color:#ecfdf5;
  box-shadow:0 8px 18px rgba(6,95,70,.22), inset 0 1px 0 rgba(255,255,255,.08);
}
.mtz-btn-primary [data-testid="stButton"] button:hover{
  background:linear-gradient(180deg, rgba(16,185,129,.36), rgba(5,150,105,.28));
  border-color:rgba(52,211,153,.48);
  box-shadow:0 12px 22px rgba(6,95,70,.28), inset 0 1px 0 rgba(255,255,255,.10);
}
.mtz-btn-primary [data-testid="stButton"] button p,
.mtz-btn-neutral [data-testid="stButton"] button p,
.mtz-btn-ghost [data-testid="stButton"] button p{font-weight:700}

/* ===== Espaçamento premium da navegação para a seção ativa ===== */
.mtz-focus-row{
  display:flex;
  align-items:center;
  margin:10px 0 12px 0;
}
.mtz-matrix-gap{
  height:2px;
  margin:0 0 8px 0;
}
.mtz-focus-chip{
  gap:8px;
  padding:0 16px;
  min-height:40px;
  border-color:rgba(16,185,129,.26);
  background:linear-gradient(180deg, rgba(9,54,43,.72), rgba(6,35,29,.82));
  box-shadow:inset 0 1px 0 rgba(255,255,255,.05), 0 8px 18px rgba(3,17,14,.18);
  transition:transform .18s ease, box-shadow .18s ease, border-color .18s ease, background .18s ease;
}
.mtz-focus-chip:hover{
  transform:translateY(-1px);
  border-color:rgba(52,211,153,.38);
  background:linear-gradient(180deg, rgba(10,68,53,.78), rgba(7,41,34,.88));
  box-shadow:inset 0 1px 0 rgba(255,255,255,.06), 0 12px 24px rgba(6,95,70,.18);
}
.mtz-section-wrap{
  animation:mtzFadeSlide .30s cubic-bezier(.22,.61,.36,1);
  will-change:opacity, transform;
}
@keyframes mtzFadeSlide{
  from{opacity:0;transform:translateY(8px);}
  to{opacity:1;transform:translateY(0);}
}
.mtz-sector-content-fade{
  animation:mtzSectorFade .22s ease-out;
}
@keyframes mtzSectorFade{
  from{opacity:0;transform:translateY(6px);}
  to{opacity:1;transform:translateY(0);}
}
</style>""", unsafe_allow_html=True)




def _truncate_card_title(value: str, limit: int = 18) -> str:
    value = (value or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(limit - 1, 1)].rstrip() + "…"


def _compact_card_summary(pct: int, eqc: int, svc: int) -> str:
    return f"{int(eqc)} eq · {int(svc)} svc"


def _truncate_card_subtitle(value: str, limit: int = 16) -> str:
    value = (value or "").strip()
    if not value:
        return "Sem departamento"
    if len(value) <= limit:
        return value
    return value[: max(limit - 1, 1)].rstrip() + "…"


def _card_status_meta(pct: int, eqc: int, svc: int) -> tuple[str, str]:
    if eqc == 0 or svc == 0:
        return "⬜ Sem dados", "sem-dados"
    if pct < 50:
        return "🔴 Crítico", "critico"
    if pct < 80:
        return "🟡 Atenção", "atencao"
    return "🟢 Avançado", "avancado"


def _build_group_card_label(nome: str, dept_lbl: str, pct: int, eqc: int, svc: int) -> str:
    title = _truncate_card_title(nome, 18)
    subtitle = _truncate_card_subtitle(dept_lbl, 16)
    status_txt, _ = _card_status_meta(pct, eqc, svc)
    metrics = f"{int(pct)}%  •  {int(eqc)} eq  •  {int(svc)} svc"
    return f"{title}\n{subtitle}\n{metrics}\n{status_txt}   ↗ Abrir"


def _card_status_badge(pct: int, eqc: int, svc: int) -> tuple[str, str]:
    if eqc == 0 or svc == 0:
        return "⬜ Sem dados", "sem-dados"
    if pct < 50:
        return "🔴 Crítico", "critico"
    if pct < 80:
        return "🟡 Atenção", "atencao"
    return "🟢 Avançado", "avancado"


def _build_group_card_html(nome: str, dept_lbl: str, pct: int, eqc: int, svc: int) -> str:
    title = _truncate_card_title(nome, 20)
    subtitle = _truncate_card_subtitle(dept_lbl, 18)
    status_txt, status_cls = _card_status_meta(pct, eqc, svc)
    metrics = f"{int(pct)}% <span>•</span> {int(eqc)} eq <span>•</span> {int(svc)} svc"
    return f"""
    <div class="mtz-group-card {status_cls}">
        <div class="mtz-group-card__title">{title}</div>
        <div class="mtz-group-card__subtitle">{subtitle}</div>
        <div class="mtz-group-card__metrics">{metrics}</div>
        <div class="mtz-group-card__footer">
            <span class="mtz-group-card__status">{status_txt}</span>
            <span class="mtz-group-card__cta">↗ Abrir</span>
        </div>
    </div>
    """



def _pct_bar_html(pct: int, height: int = 6) -> str:
    color = _risk_color(pct)
    w = max(0, min(100, pct))
    h = max(height, 8)
    return (
        f'<div class="mtz-pct-outer" style="height:{h}px">'
        f'<div class="mtz-pct-inner" style="width:{w}%;background:{color};height:{h}px;transition:width .25s ease"></div>'
        f'</div>'
    )



/* ===== Navegação rápida compacta ===== */
.mtz-quick-nav-compact-label{
  margin:6px 0 6px 0;
  font-size:.84rem;
  font-weight:700;
  letter-spacing:.02em;
  text-transform:uppercase;
  color:rgba(255,255,255,.72);
}
.mtz-quick-nav-compact{
  margin:0 0 10px 0;
}
.mtz-quick-nav-compact [data-testid="stSegmentedControl"],
.mtz-quick-nav-compact [role="radiogroup"]{
  margin-top:0;
}
.mtz-quick-nav-compact [data-testid="stSegmentedControl"] label,
.mtz-quick-nav-compact [role="radio"]{
  min-height:34px !important;
}
.mtz-section-wrap{
  margin-top:2px;
}

/* chip de foco removido da UI */
.mtz-focus-row,.mtz-focus-chip,.mtz-focus-chip-wrap,.mtz-matrix-gap{display:none !important;}
