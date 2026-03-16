from __future__ import annotations

from html import escape
from typing import Mapping

import streamlit as st

_TONE = {
    "info": {"bg": "rgba(59,130,246,.12)", "bd": "rgba(59,130,246,.32)", "title": "#DBEAFE", "body": "#BFDBFE"},
    "success": {"bg": "rgba(34,197,94,.12)", "bd": "rgba(34,197,94,.30)", "title": "#DCFCE7", "body": "#BBF7D0"},
    "warning": {"bg": "rgba(245,158,11,.12)", "bd": "rgba(245,158,11,.30)", "title": "#FEF3C7", "body": "#FDE68A"},
    "danger": {"bg": "rgba(239,68,68,.12)", "bd": "rgba(239,68,68,.28)", "title": "#FEE2E2", "body": "#FECACA"},
    "neutral": {"bg": "rgba(148,163,184,.10)", "bd": "rgba(148,163,184,.24)", "title": "#E2E8F0", "body": "#CBD5E1"},
}


def notice_card(title: str, body: str, *, tone: str = "info") -> None:
    theme = _TONE.get(tone, _TONE["info"])
    st.markdown(
        (
            '<div style="margin:.35rem 0 .8rem 0;padding:.85rem 1rem;border-radius:14px;'
            f'background:{theme["bg"]};border:1px solid {theme["bd"]}">'
            f'<div style="font-weight:700;color:{theme["title"]};margin-bottom:.2rem">{escape(title)}</div>'
            f'<div style="color:{theme["body"]};font-size:.95rem">{escape(body)}</div>'
            '</div>'
        ),
        unsafe_allow_html=True,
    )


def selection_summary(title: str, items: Mapping[str, object], *, caption: str | None = None) -> None:
    chips: list[str] = []
    for label, value in items.items():
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        chips.append(
            '<span style="display:inline-block;padding:.34rem .58rem;border-radius:999px;'
            'margin:.15rem .32rem .15rem 0;background:rgba(255,255,255,.06);'
            'border:1px solid rgba(255,255,255,.10);font-size:.84rem;color:#E5EDF7">'
            f'<strong style="font-weight:700">{escape(label)}:</strong> {escape(text)}'
            '</span>'
        )

    if not chips and not caption:
        return

    cap_html = (
        f'<div style="color:#94A3B8;font-size:.82rem;margin-top:.35rem">{escape(caption)}</div>' if caption else ''
    )
    st.markdown(
        (
            '<div style="margin:.35rem 0 .95rem 0;padding:.8rem .95rem;border-radius:16px;'
            'background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.08)">'
            f'<div style="font-weight:700;color:#F8FAFC;margin-bottom:.35rem">{escape(title)}</div>'
            f'<div>{"".join(chips)}</div>{cap_html}'
            '</div>'
        ),
        unsafe_allow_html=True,
    )
