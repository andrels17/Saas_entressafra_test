"""Utilitários compartilhados entre módulos admin (Grupos, Departamentos, etc.)."""
from __future__ import annotations

import re
import unicodedata

import streamlit as st
from src.ui.core.design_system import inject_design_system_css


# ─── CSS ────────────────────────────────────────────────────────────────

def inject_enterprise_css() -> None:
    """CSS comum para cards e badges dos painéis admin."""
    inject_design_system_css()
    st.markdown(
        """
<style>
.card-enterprise {
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(255,255,255,0.10);
  border-radius: 14px;
  padding: 12px 14px;
  margin: 10px 0 10px 0;
  transition: all .18s ease-in-out;
}
.card-enterprise:hover {
  border-color: rgba(255,75,75,0.55);
  box-shadow: 0 0 0 2px rgba(255,75,75,0.08) inset;
}
.badge {
  display: inline-block;
  padding: 3px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  border: 1px solid rgba(255,255,255,0.10);
  margin-right: 6px;
  line-height: 1.35;
}
.badge-active  { background: rgba(255,75,75,0.14);  color: #ff4b4b; border-color: rgba(255,75,75,0.35); }
.badge-inactive{ background: rgba(180,180,180,0.12); color: rgba(235,235,235,0.72); }
.badge-neutral { background: rgba(255,255,255,0.06); color: rgba(235,235,235,0.92); }
.small-muted   { color: rgba(235,235,235,0.70); font-size: 12px; }
</style>
""",
        unsafe_allow_html=True,
    )


# ─── Paginação ──────────────────────────────────────────────────────────

def clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))


def pager(key: str, total: int, page_size: int) -> tuple[int, int]:
    """Paginador simples. Retorna (page_idx, total_pages)."""
    total_pages = max(1, (total + page_size - 1) // page_size)
    ss_key = f"{key}__page"
    if ss_key not in st.session_state:
        st.session_state[ss_key] = 0
    st.session_state[ss_key] = clamp(
        int(st.session_state[ss_key]), 0, total_pages - 1)
    page_idx = int(st.session_state[ss_key])

    c1, c2, c3, c4 = st.columns([1, 2, 2, 1], gap="small")
    with c1:
        if st.button(
            "◀",
            key=f"{key}__prev",
            use_container_width=True,
            disabled=(
                page_idx <= 0)):
            st.session_state[ss_key] = page_idx - 1
            st.rerun()
    with c2:
        st.caption(f"Página **{page_idx + 1}** de **{total_pages}**")
    with c3:
        go = st.number_input(
            "Ir para",
            min_value=1, max_value=total_pages,
            value=page_idx + 1, step=1,
            key=f"{key}__goto",
            label_visibility="collapsed",
        )
        if int(go) - 1 != page_idx:
            st.session_state[ss_key] = int(go) - 1
            st.rerun()
    with c4:
        if st.button(
            "▶",
            key=f"{key}__next",
            use_container_width=True,
            disabled=(
                page_idx >= total_pages -
                1)):
            st.session_state[ss_key] = page_idx + 1
            st.rerun()

    return int(st.session_state[ss_key]), total_pages


# ─── Misc ───────────────────────────────────────────────────────────────

def safe_rerun() -> None:
    """st.rerun compatível com o módulo nav legado."""
    try:
        import nav  # type: ignore
        if hasattr(nav, "rerun_keep_menu"):
            nav.rerun_keep_menu()
            return
    except Exception:
        pass
    st.rerun()


def norm_name(s: str) -> str:
    """Normaliza nome para comparação/deduplicação (sem acentos, lowercase)."""
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()
