"""Página de notificações — Feed in-app + Exportar + E-mail."""
from __future__ import annotations

import streamlit as st

from src.auth.roles import Role
from src.auth.scope import get_my_scope
from src.auth.permissions import can_view_all_data
from src.ui.core.styles import page_header as _ph
from src.ui.components.feedback import notice_card
from src.ui.components.actions import refresh_button
from src.ui.core.cache import bump_data_version, clear_cached_functions
from src.utils.supabase_helpers import current_role, current_tenant_id
from src.utils.nav import get_current_revisao

from .data import load_data, build_alertas
from .fragments import (
    fragment_disparo_manual,
    fragment_configurar_agendamento,
)

# ── Metadados visuais por categoria ─────────────────────────────────────────
_CATS = [
    ("travados",    "⛔", "Travados",       "#C53030", "rgba(197,48,48,0.12)"),
    ("sem_update",  "⏸",  "Parados",        "#D69E2E", "rgba(214,158,46,0.12)"),
    ("risco_prazo", "⚠️", "Risco de prazo", "#D69E2E", "rgba(214,158,46,0.10)"),
    ("sem_inicio",  "⬜", "Sem início",      "#718096", "rgba(113,128,150,0.10)"),
]


def _lidos_key(revisao_id: str) -> str:
    return f"_ntf_lidos_{revisao_id}"


def _marcar_lido(revisao_id: str, cat: str) -> None:
    key = _lidos_key(revisao_id)
    lidos: set = st.session_state.get(key, set())
    lidos.add(cat)
    st.session_state[key] = lidos


def _is_lido(revisao_id: str, cat: str) -> bool:
    return cat in st.session_state.get(_lidos_key(revisao_id), set())


def _render_header_card(alertas: dict, revisao: dict) -> None:
    """Card de resumo no topo com status geral e métricas."""
    n_trav = len(alertas["travados"])
    n_sem  = len(alertas["sem_inicio"])
    n_par  = len(alertas["sem_update"])
    n_risc = len(alertas["risco_prazo"])
    total  = n_trav + n_sem + n_par + n_risc

    if n_trav > 0:
        bar_color, status_txt = "#C53030", "Atenção crítica"
    elif (n_par + n_risc) > 0:
        bar_color, status_txt = "#D69E2E", "Requer atenção"
    else:
        bar_color, status_txt = "#38A169", "Tudo em ordem"

    sem     = alertas["semana_atual"]
    tot_sem = alertas["semanas_total"]
    titulo  = revisao.get("titulo", "—")

    col_info, col_num = st.columns([3, 1])
    with col_info:
        st.markdown(
            f'<div style="font-size:0.68rem;font-weight:600;color:#8A9BAE;'
            f'letter-spacing:0.08em;text-transform:uppercase;margin-bottom:3px">'
            f'Revisão ativa</div>'
            f'<div style="font-size:1.05rem;font-weight:700;color:#E8EDF5;margin-bottom:2px">'
            f'{titulo}</div>'
            f'<div style="font-size:0.78rem;color:#8A9BAE">'
            f'Semana <b style="color:#E8EDF5">{sem}</b> de '
            f'<b style="color:#E8EDF5">{tot_sem}</b></div>',
            unsafe_allow_html=True,
        )
    with col_num:
        st.metric(
            "Alertas ativos",
            total,
            delta=status_txt,
            delta_color="inverse" if total > 0 else "off",
        )

    st.divider()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("⛔ Travados",       n_trav, delta_color="inverse" if n_trav else "off",
              delta="crítico" if n_trav else "ok")
    c2.metric("⬜ Sem início",     n_sem,  delta_color="inverse" if n_sem  else "off",
              delta="atenção" if n_sem  else "ok")
    c3.metric("⏸ Parados",         n_par,  delta_color="inverse" if n_par  else "off",
              delta="atenção" if n_par  else "ok")
    c4.metric("⚠️ Risco de prazo", n_risc, delta_color="inverse" if n_risc else "off",
              delta="atraso"  if n_risc else "no prazo")


def _render_alert_card(cat: str, icon: str, label: str, color: str, bg: str,
                       df, revisao_id: str) -> None:
    """Card individual de alerta com detalhes inline expansíveis."""
    lido = _is_lido(revisao_id, cat)
    n = len(df)

    if n == 0 and cat != "travados":
        return  # oculta categorias vazias exceto travados

    border = f"rgba(255,255,255,0.06)" if lido else f"{color}55"
    left_border = "rgba(255,255,255,0.10)" if lido else color
    card_bg = "rgba(255,255,255,0.02)" if lido else bg
    text_opacity = "0.5" if lido else "1"

    # ── Card header ──
    with st.container():
        col_icon, col_text, col_badge = st.columns([0.08, 0.7, 0.22])
        with col_icon:
            st.markdown(
                f'<div style="font-size:1.4rem;line-height:1;padding-top:4px;'
                f'opacity:{text_opacity}">{icon}</div>',
                unsafe_allow_html=True,
            )
        with col_text:
            ocorrencias = "Nenhuma ocorrência" if n == 0 else f"{n} ocorrência{'s' if n > 1 else ''}"
            st.markdown(
                f'<div style="opacity:{text_opacity}">'
                f'<b style="font-size:0.92rem;color:#E8EDF5">{label}</b><br>'
                f'<span style="font-size:0.75rem;color:#8A9BAE">{ocorrencias}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with col_badge:
            if lido:
                st.markdown(
                    '<div style="text-align:right;padding-top:4px">'
                    '<span style="font-size:0.68rem;color:#38A169;font-weight:600;'
                    'background:rgba(56,161,105,0.12);padding:3px 10px;border-radius:999px;'
                    'border:1px solid rgba(56,161,105,0.3)">✓ Visto</span></div>',
                    unsafe_allow_html=True,
                )
            elif n > 0:
                if st.button(
                    "Marcar como visto",
                    key=f"ntf_lido_{cat}",
                    type="tertiary",
                    use_container_width=True,
                ):
                    _marcar_lido(revisao_id, cat)
                    st.rerun()

        # Linha colorida abaixo do card
        st.markdown(
            f'<div style="height:2px;background:linear-gradient('
            f'90deg,{left_border},transparent);border-radius:999px;margin:4px 0 10px"></div>',
            unsafe_allow_html=True,
        )

    # ── Detalhes inline (expander) ──
    if n > 0 and not lido:
        with st.expander(f"Ver {n} ocorrência{'s' if n > 1 else ''}", expanded=False):
            cols_priority = {
                "travados":    ["Frota", "Modelo", "Grupo", "Setor", "Serviço", "Dias travado", "Obs."],
                "sem_update":  ["Frota", "Modelo", "Grupo", "Setor", "Serviço", "Dias parado"],
                "risco_prazo": ["Frota", "Modelo", "Grupo", "% Atual", "% Esperado", "Atraso (p.p.)"],
                "sem_inicio":  ["Frota", "Modelo", "Grupo", "Setor", "Serviço", "Dias sem update"],
            }
            cols_show = [c for c in cols_priority.get(cat, df.columns.tolist())
                         if c in df.columns]
            sort_col = {"travados": "Dias travado", "sem_update": "Dias parado",
                        "risco_prazo": "Atraso (p.p.)", "sem_inicio": "Dias sem update"}.get(cat)
            df_show = df[cols_show].sort_values(sort_col, ascending=False) \
                if sort_col and sort_col in df.columns else df[cols_show]
            st.dataframe(df_show, use_container_width=True, hide_index=True)


def render_notificacoes() -> None:
    _ph("🔔", "Notificações", "Alertas proativos da revisão ativa.")

    tenant_id = current_tenant_id()
    if not tenant_id:
        st.info("Selecione um tenant para ver as notificações.")
        return

    ver = str(st.session_state.get("data_version", "0"))
    revisao_id = get_current_revisao()
    if not revisao_id:
        notice_card(
            "Nenhuma revisão selecionada",
            "Abra uma revisão ativa pela Matriz ou Home para visualizar os alertas.",
            tone="warning",
        )
        return

    # ── Thresholds ────────────────────────────────────────────────────────────
    with st.expander("⚙️ Configurar thresholds", expanded=False):
        tc1, tc2 = st.columns(2)
        with tc1:
            dias_travado = st.number_input(
                "Alertar travado há (dias)", min_value=1, max_value=30, value=2, step=1,
                key="ntf_dias_trav")
        with tc2:
            dias_sem_update = st.number_input(
                "Alertar parado há (dias)", min_value=1, max_value=30, value=5, step=1,
                key="ntf_dias_upd")

    # ── Dados ─────────────────────────────────────────────────────────────────
    with st.spinner("", show_time=False):
        raw = load_data(tenant_id, revisao_id, ver, st.session_state.get("sb_access_token", ""))

    tarefas = raw["tarefas"]
    revisao = raw["revisao"]

    if not tarefas:
        notice_card("Revisão sem tarefas",
                    "A revisão selecionada ainda não possui tarefas sincronizadas.",
                    tone="warning")
        return

    # ── Escopo ────────────────────────────────────────────────────────────────
    role = current_role()
    is_admin = Role.is_admin(role)
    dep_ids, grp_ids = get_my_scope(tenant_id)
    if not can_view_all_data(role):
        if dep_ids == [] and grp_ids == []:
            st.warning("Você não possui departamentos ou grupos vinculados.")
            return
        if grp_ids is not None:
            tarefas = [t for t in tarefas
                       if (t.get("equipamentos") or {}).get("grupo_id") in grp_ids]

    alertas = build_alertas(tarefas, revisao, int(dias_travado), int(dias_sem_update))

    # ── Abas: Feed | Exportar | E-mail ────────────────────────────────────────
    tab_feed, tab_export, tab_email = st.tabs(["🔔 Feed", "⬇️ Exportar", "📧 E-mail"])

    # ── Feed ──────────────────────────────────────────────────────────────────
    with tab_feed:
        _render_header_card(alertas, revisao)

        total_alertas = sum(len(alertas[c]) for c in
                            ["travados", "sem_inicio", "sem_update", "risco_prazo"])

        if total_alertas == 0:
            st.divider()
            notice_card(
                "Tudo em ordem ✅",
                "Nenhuma ocorrência encontrada com os parâmetros configurados.",
                tone="success",
            )
        else:
            # Ações rápidas
            col_mark, col_clear, _ = st.columns([1.4, 1.2, 2])
            with col_mark:
                if st.button("✓ Marcar tudo como visto", key="ntf_mark_all",
                             use_container_width=True, type="tertiary"):
                    for cat, *_ in _CATS:
                        _marcar_lido(revisao_id, cat)
                    st.rerun()
            with col_clear:
                if st.button("↺ Limpar marcações", key="ntf_clear_lidos",
                             use_container_width=True, type="tertiary"):
                    st.session_state.pop(_lidos_key(revisao_id), None)
                    st.rerun()

            st.divider()

            # Cards de alerta
            for cat, icon, label, color, bg in _CATS:
                _render_alert_card(cat, icon, label, color, bg,
                                   alertas[cat], revisao_id)

    # ── Exportar ──────────────────────────────────────────────────────────────
    with tab_export:
        from .pdf import build_pdf_alertas
        from src.ui.components.actions import download_action

        st.markdown("### ⬇️ Exportações")
        st.caption("Baixe os alertas em formato CSV por categoria ou PDF consolidado.")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**CSV por categoria**")
            for cat_key, _, cat_label, _, _ in _CATS:
                df = alertas[cat_key]
                if not df.empty:
                    cols_pub = [c for c in df.columns if c != "dept_id"]
                    st.download_button(
                        f"⬇️ {cat_label}",
                        data=df[cols_pub].to_csv(index=False).encode("utf-8"),
                        file_name=f"alertas_{cat_key}.csv", mime="text/csv",
                        use_container_width=True, key=f"dl_exp_{cat_key}",
                    )
        with col2:
            st.markdown("**PDF consolidado**")
            try:
                import reportlab  # noqa: F401
                pdf_bytes = build_pdf_alertas(alertas, revisao)
                titulo_rev = (revisao.get("titulo") or "revisao").replace("/", "-")
                download_action(
                    "⬇️ Baixar PDF completo", data=pdf_bytes,
                    file_name=f"alertas_{titulo_rev}.pdf",
                    mime="application/pdf", key="ntf_pdf_dl", type="primary",
                )
            except ImportError:
                st.info("Instale `reportlab` para habilitar exportação em PDF.")

    # ── E-mail (intocado) ─────────────────────────────────────────────────────
    with tab_email:
        st.markdown("### Envio de Relatório por E-mail")
        st.caption("Envie manualmente ou configure o agendamento automático por departamento.")
        st.divider()
        fragment_disparo_manual(tenant_id, revisao_id, is_admin,
                                int(dias_travado), int(dias_sem_update))
        st.divider()
        fragment_configurar_agendamento(tenant_id, is_admin)

    # ── Atualizar ─────────────────────────────────────────────────────────────
    if refresh_button("ntf_refresh", label="Atualizar alertas",
                      help="Reprocessa os alertas com os dados atuais."):
        bump_data_version()
        clear_cached_functions(load_data)
        st.rerun()
