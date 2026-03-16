
import streamlit as st
from typing import Optional, Callable, Any, List, Tuple
from .styles import render_status_chip


EVENT_LABELS = {
    "tarefa_created": "Criado",
    "tarefa_updated": "Atualizado",
}


def clamp_text(text: str, max_lines: int = 2) -> str:
    """Retorna HTML com truncagem por número de linhas + tooltip com texto completo."""
    safe = (
        text or "").replace(
        "&",
        "&amp;").replace(
            "<",
            "&lt;").replace(
                ">",
                "&gt;").replace(
                    '"',
        "&quot;")
    cls = f"ea-clamp ea-clamp-{max_lines}"
    return f'<span class="{cls}" title="{safe}">{safe}</span>'


def render_empty_state(title: str,
                       subtitle: str = "",
                       cta_label: str = "",
                       cta_key: str = "",
                       on_cta: Optional[Callable[[],
                                                 Any]] = None):
    st.markdown(
        f'''
        <div class="ea-empty">
          <div class="ea-empty-title">{title}</div>
          <div class="ea-empty-sub">{subtitle}</div>
        </div>
        ''',
        unsafe_allow_html=True
    )
    if cta_label:
        if st.button(
                cta_label,
                key=cta_key or f"cta_{title}",
                use_container_width=True):
            if on_cta:
                on_cta()


def row_actions(
        label: str, actions: List[Tuple[str, Callable[[], Any]]], key: str):
    """Menu de ações compacto por linha. Usa popover quando disponível."""
    if hasattr(st, "popover"):
        with st.popover("⋯", use_container_width=False):
            st.markdown(f"**{label}**")
            for a_label, fn in actions:
                if st.button(a_label, key=f"{key}_{a_label}"):
                    fn()
    else:
        # fallback
        with st.expander("⋯", expanded=False):
            st.markdown(f"**{label}**")
            for a_label, fn in actions:
                if st.button(a_label, key=f"{key}_{a_label}"):
                    fn()


def chip(status: str) -> None:
    """Exibe chip de status — usa st.badge nativo (1.42+)."""
    render_status_chip(status)


def render_tarefa_history(sb, tenant_id: str, tarefa_id: str, limit: int = 8):
    """Mostra histórico (auditoria) enxuto dentro do painel lateral.

    Mantém UX rápida: sem joins pesados, sem dataframe.
    """
    if not tarefa_id:
        return

    try:
        rows = (
            sb.table("historico_eventos")
            .select("id,created_at,evento,user_id,detalhes")
            .eq("tenant_id", tenant_id)
            .eq("tarefa_id", tarefa_id)
            .order("created_at", desc=True)
            .limit(int(limit))
            .execute()
            .data
        ) or []
    except Exception:
        rows = []

    st.markdown("#### Histórico")
    if not rows:
        st.caption("Sem eventos registrados para esta tarefa.")
        return

    for r in rows:
        when = str(r.get("created_at") or "—")
        ev = EVENT_LABELS.get(r.get("evento"), r.get("evento") or "—")
        detalhes = r.get("detalhes") or {}
        # resumo leve
        resumo = ""
        if isinstance(detalhes, dict):
            old = detalhes.get("old")
            new = detalhes.get("new")
            if isinstance(old, dict) and isinstance(new, dict):
                o = old.get("status")
                n = new.get("status")
                if o or n:
                    resumo = f"{o or '—'} → {n or '—'}"

        cols = st.columns([0.55, 0.45])
        cols[0].markdown(f"**{ev}**  ")
        cols[0].caption(when)
        cols[1].markdown(
            clamp_text(
                resumo,
                2) if resumo else "",
            unsafe_allow_html=True)
