"""Funções auxiliares reutilizáveis da página de matriz."""
from __future__ import annotations

import pandas as pd
import streamlit as st


def _inject_css():
    st.markdown("""<style>
.enterprise-sticky{position:sticky;top:0;z-index:999;padding:12px 12px 10px 12px;
margin:0 0 12px 0;border-radius:16px;background:rgba(18,18,18,.86);
backdrop-filter:blur(10px);border:1px solid rgba(255,255,255,.08);
box-shadow:0 8px 24px rgba(0,0,0,.35);}
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
.mtz-card-grid{margin-top:6px}
.mtz-card-grid [data-testid="stButton"] button{
  width:100%;text-align:left;padding:14px;border-radius:18px;
  border:1px solid rgba(255,255,255,.10);background:rgba(255,255,255,.04);
  color:rgba(255,255,255,.92);box-shadow:0 8px 22px rgba(0,0,0,.25);
  transition:transform .08s ease,border-color .12s ease,background .12s ease;}
.mtz-card-grid [data-testid="stButton"] button:hover{
  transform:translateY(-1px);border-color:rgba(255,255,255,.18);background:rgba(255,255,255,.06);}
</style>""", unsafe_allow_html=True)


def _risk_color(pct: int) -> str:
    if pct >= 80:
        return "#12B76A"
    if pct >= 50:
        return "#F59E0B"
    return "#EF4444"


def _risk_score(pct: int) -> int:
    score = 100 - int(pct)
    if pct < 50:
        score += 15
    if pct < 30:
        score += 20
    return int(score)


def _pct_bar_html(pct: int, height: int = 6) -> str:
    color = _risk_color(pct)
    w = max(0, min(100, pct))
    return (
        f'<div style="background:rgba(255,255,255,.08);border-radius:4px;height:{height}px;margin-top:6px">'
        f'<div style="width:{w}%;background:{color};height:{height}px;border-radius:4px;transition:width .3s"></div>'
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
