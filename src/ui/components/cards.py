from html import escape as _h

import streamlit as st


def metric_card(
        title: str,
        value: str,
        subtitle: str = "",
        accent: str = "primary"):
    accent_map = {
        "primary": "#FFD100",
        "warn": "#F97316",
        "ok": "#22C55E",
        "neutral": "#94A3B8",
        "danger": "#EF4444"}
    line = accent_map.get(accent, accent_map["primary"])
    st.markdown(
        f'''<div class="ds-card metric-card" style="--accent:{line}"><div class="metric-card__title">{_h(str(title))}</div><div class="metric-card__value">{_h(str(value))}</div><div class="metric-card__subtitle">{_h(str(subtitle))}</div></div>''',
        unsafe_allow_html=True)


def info_card(title: str, body: str, tone: str = "default"):
    tone_cls = {"default": "info-card--default",
                "success": "info-card--success",
                "warning": "info-card--warning",
                "danger": "info-card--danger"}.get(tone,
                                                   "info-card--default")
    st.markdown(
        f'''<div class="ds-card info-card {tone_cls}"><div class="info-card__title">{_h(str(title))}</div><div class="info-card__body">{body}</div></div>''',
        unsafe_allow_html=True)
