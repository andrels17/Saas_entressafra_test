"""Página de notificações — ponto de entrada público."""
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
    fragment_travados, fragment_sem_inicio,
    fragment_parados, fragment_risco_prazo, fragment_resumo_grupos,
    fragment_disparo_manual, fragment_configurar_agendamento,
)


# ── Cores e helpers visuais ──────────────────────────────────────────────────

_CAT_META = {
    "travados":    {"icon": "⛔", "label": "Travados",      "color": "#C53030", "bg": "rgba(197,48,48,0.12)"},
    "sem_inicio":  {"icon": "⬜", "label": "Sem início",    "color": "#718096", "bg": "rgba(113,128,150,0.12)"},
    "sem_update":  {"icon": "⏸",  "label": "Parados",       "color": "#D69E2E", "bg": "rgba(214,158,46,0.12)"},
    "risco_prazo": {"icon": "⚠️", "label": "Risco de prazo","color": "#D69E2E", "bg": "rgba(214,158,46,0.12)"},
}


def _lidos_key(revisao_id: str) -> str:
    return f"_ntf_lidos_{revisao_id}"


def _marcar_lido(revisao_id: str, cat: str) -> None:
    key = _lidos_key(revisao_id)
    lidos: set = st.session_state.get(key, set())
    lidos.add(cat)
    st.session_state[key] = lidos


def _is_lido(revisao_id: str, cat: str) -> bool:
    return cat in st.session_state.get(_lidos_key(revisao_id), set())


def _render_feed_card(cat: str, df, revisao_id: str) -> None:
    """Renderiza um card de alerta no feed com marcação de lido."""
    meta = _CAT_META[cat]
    lido = _is_lido(revisao_id, cat)
    n = len(df)

    border_color = "rgba(255,255,255,0.06)" if lido else meta["color"] + "55"
    opacity = "0.55" if lido else "1"
    lido_badge = (
        '<span style="font-size:0.68rem;color:#38A169;font-weight:600;'
        'background:rgba(56,161,105,0.12);padding:2px 8px;border-radius:999px;'
        'border:1px solid rgba(56,161,105,0.3)">✓ Visto</span>'
        if lido else ""
    )

    st.markdown(
        f"""
        <div style="
            border:1px solid {border_color};
            border-left: 3px solid {meta['color'] if not lido else 'rgba(255,255,255,0.1)'};
            border-radius:10px;
            padding:14px 16px;
            margin-bottom:10px;
            background:{meta['bg'] if not lido else 'rgba(255,255,255,0.02)'};
            opacity:{opacity};
        ">
            <div style="display:flex;align-items:center;justify-content:space-between;gap:10px">
                <div style="display:flex;align-items:center;gap:10px">
                    <span style="font-size:1.3rem">{meta['icon']}</span>
                    <div>
                        <div style="font-weight:700;font-size:0.9rem;color:#E8EDF5">
                            {meta['label']}
                        </div>
                        <div style="font-size:0.78rem;color:#8A9BAE;margin-top:1px">
                            {'Nenhuma ocorrência' if n == 0 else f'{n} ocorrência{"s" if n > 1 else ""}'}
                        </div>
                    </div>
                </div>
                {lido_badge}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_resumo_header(alertas: dict, revisao: dict) -> None:
    """Card de resumo visual no topo da página."""
    sem = alertas["semana_atual"]
    tot = alertas["semanas_total"]
    n_trav = len(alertas["travados"])
    n_sem  = len(alertas["sem_inicio"])
    n_par  = len(alertas["sem_update"])
    n_risc = len(alertas["risco_prazo"])
    total  = n_trav + n_sem + n_par + n_risc

    rev_titulo = revisao.get("titulo", "—")

    # Cor geral: vermelho se travados, amarelo se outros alertas, verde se tudo ok
    if n_trav > 0:
        bar_color, status_txt = "#C53030", "Atenção crítica"
    elif (n_par + n_risc) > 0:
        bar_color, status_txt = "#D69E2E", "Requer atenção"
    else:
        bar_color, status_txt = "#38A169", "Tudo em ordem"

    st.markdown(
        f"""
        <div style="
            background:linear-gradient(135deg,rgba(255,255,255,0.04),rgba(255,255,255,0.02));
            border:1px solid rgba(255,255,255,0.08);
            border-left:3px solid {bar_color};
            border-radius:12px;padding:16px 20px;margin-bottom:20px
        ">
            <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px">
                <div>
                    <div style="font-size:0.68rem;font-weight:600;color:#8A9BAE;
                                letter-spacing:0.08em;text-transform:uppercase;margin-bottom:3px">
                        Revisão ativa
                    </div>
                    <div style="font-size:1rem;font-weight:700;color:#E8EDF5">{rev_titulo}</div>
                    <div style="font-size:0.78rem;color:#8A9BAE;margin-top:2px">
                        Semana <b style="color:#E8EDF5">{sem}</b> de <b style="color:#E8EDF5">{tot}</b>
                    </div>
                </div>
                <div style="text-align:right">
                    <div style="font-size:1.4rem;font-weight:800;color:{bar_color}">{total}</div>
                    <div style="font-size:0.72rem;color:#8A9BAE">alerta{"s" if total != 1 else ""} ativos</div>
                    <div style="font-size:0.72rem;color:{bar_color};font-weight:600;margin-top:2px">{status_txt}</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Métricas rápidas
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("⛔ Travados",      n_trav, delta_color="inverse" if n_trav else "off",
              delta="crítico" if n_trav else "ok")
    c2.metric("⬜ Sem início",    n_sem,  delta_color="inverse" if n_sem  else "off",
              delta="atenção" if n_sem else "ok")
    c3.metric("⏸ Parados",        n_par,  delta_color="inverse" if n_par  else "off",
              delta="atenção" if n_par else "ok")
    c4.metric("⚠️ Risco de prazo", n_risc, delta_color="inverse" if n_risc else "off",
              delta="atraso" if n_risc else "no prazo")


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

    # ── Thresholds (colapsado por padrão) ────────────────────────────────────
    with st.expander("⚙️ Configurar thresholds", expanded=False):
        tc1, tc2 = st.columns(2)
        with tc1:
            dias_travado = st.number_input(
                "Alertar travado há (dias)", min_value=1, max_value=30, value=2, step=1,
                key="ntf_dias_trav", help="Tarefas com status 'travado' há pelo menos X dias.")
        with tc2:
            dias_sem_update = st.number_input(
                "Alertar parado há (dias)", min_value=1, max_value=30, value=5, step=1,
                key="ntf_dias_upd", help="Tarefas não concluídas sem atualização há X dias.")

    # ── Carregamento ──────────────────────────────────────────────────────────
    with st.spinner("", show_time=False):
        raw = load_data(tenant_id, revisao_id, ver, st.session_state.get("sb_access_token", ""))

    tarefas = raw["tarefas"]
    revisao = raw["revisao"]

    if not tarefas:
        notice_card("Revisão sem tarefas",
                    "A revisão selecionada ainda não possui tarefas sincronizadas.",
                    tone="warning")
        return

    # ── Filtro de escopo ──────────────────────────────────────────────────────
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

    # ── Abas principais ───────────────────────────────────────────────────────
    tab_feed, tab_trav, tab_sem, tab_par, tab_risc, tab_grupos, tab_export, tab_email = st.tabs([
        "🔔 Feed",
        f"⛔ Travados ({len(alertas['travados'])})",
        f"⬜ Sem início ({len(alertas['sem_inicio'])})",
        f"⏸ Parados ({len(alertas['sem_update'])})",
        f"⚠️ Risco ({len(alertas['risco_prazo'])})",
        "📊 Por grupo",
        "⬇️ Exportar",
        "📧 E-mail",
    ])

    # ── Feed (nova aba principal) ─────────────────────────────────────────────
    with tab_feed:
        _render_resumo_header(alertas, revisao)
        st.divider()

        total_alertas = (len(alertas["travados"]) + len(alertas["sem_inicio"])
                         + len(alertas["sem_update"]) + len(alertas["risco_prazo"]))

        if total_alertas == 0:
            notice_card(
                "Nenhum alerta crítico ✅",
                "Com os parâmetros atuais, a revisão não possui ocorrências em alerta.",
                tone="success",
            )
        else:
            col_feed, col_acoes = st.columns([3, 1])
            with col_acoes:
                st.caption("Ações")
                if st.button("✓ Marcar tudo como visto", key="ntf_mark_all",
                             use_container_width=True, type="tertiary"):
                    for cat in ["travados", "sem_inicio", "sem_update", "risco_prazo"]:
                        _marcar_lido(revisao_id, cat)
                    st.rerun()
                if st.button("↺ Limpar marcações", key="ntf_clear_lidos",
                             use_container_width=True, type="tertiary"):
                    st.session_state.pop(_lidos_key(revisao_id), None)
                    st.rerun()

            with col_feed:
                st.caption("Clique em uma categoria para ver os detalhes nas abas.")
                for cat, df in [
                    ("travados",    alertas["travados"]),
                    ("sem_update",  alertas["sem_update"]),
                    ("risco_prazo", alertas["risco_prazo"]),
                    ("sem_inicio",  alertas["sem_inicio"]),
                ]:
                    if len(df) > 0 or cat == "travados":
                        _render_feed_card(cat, df, revisao_id)
                        if len(df) > 0 and not _is_lido(revisao_id, cat):
                            if st.button(
                                f"Marcar '{_CAT_META[cat]['label']}' como visto",
                                key=f"ntf_lido_{cat}",
                                type="tertiary",
                            ):
                                _marcar_lido(revisao_id, cat)
                                st.rerun()

    # ── Abas de detalhe ───────────────────────────────────────────────────────
    with tab_trav:
        fragment_travados(alertas["travados"])

    with tab_sem:
        fragment_sem_inicio(alertas["sem_inicio"])

    with tab_par:
        fragment_parados(alertas["sem_update"])

    with tab_risc:
        fragment_risco_prazo(alertas["risco_prazo"])

    with tab_grupos:
        fragment_resumo_grupos(alertas)

    with tab_export:
        from .pdf import build_pdf_alertas
        from src.ui.components.actions import download_action
        st.markdown("### ⬇️ Exportações")
        st.caption("Baixe os alertas em formato CSV por categoria ou PDF consolidado.")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**CSV por categoria**")
            for df, label, fname in [
                (alertas["travados"],    "Travados",    "alertas_travados.csv"),
                (alertas["sem_inicio"],  "Sem início",  "alertas_sem_inicio.csv"),
                (alertas["sem_update"],  "Parados",     "alertas_parados.csv"),
                (alertas["risco_prazo"], "Risco prazo", "alertas_risco_prazo.csv"),
            ]:
                if not df.empty:
                    cols_pub = [c for c in df.columns if c != "dept_id"]
                    st.download_button(
                        f"⬇️ {label}",
                        data=df[cols_pub].to_csv(index=False).encode("utf-8"),
                        file_name=fname, mime="text/csv",
                        use_container_width=True, key=f"dl_exp_{fname}",
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

    # ── Botão atualizar ───────────────────────────────────────────────────────
    if refresh_button("ntf_refresh", label="Atualizar alertas",
                      help="Reprocessa os alertas com base nos dados atuais."):
        bump_data_version()
        clear_cached_functions(load_data)
        st.rerun()
