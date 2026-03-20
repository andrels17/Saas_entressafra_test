"""Página de notificações — ponto de entrada público."""
from __future__ import annotations

import streamlit as st

from src.auth.roles import Role
from src.auth.scope import get_my_scope
from src.auth.permissions import can_view_all_data
from src.ui.core.styles import page_header as _ph
from src.ui.components.feedback import notice_card, selection_summary
from src.ui.components.actions import download_action, refresh_button
from src.ui.core.cache import bump_data_version, clear_cached_functions
from src.utils.supabase_helpers import current_role, current_tenant_id
from src.utils.nav import get_current_revisao

from .data import load_data, build_alertas
from .pdf import build_pdf_alertas
from .fragments import (
    fragment_resumo, fragment_travados, fragment_sem_inicio,
    fragment_parados, fragment_risco_prazo, fragment_resumo_grupos,
    fragment_disparo_manual, fragment_configurar_agendamento,
)


def render_notificacoes() -> None:
    _ph("🔔", "Notificações",
        "Alertas proativos: travados, parados, sem início e risco de prazo.")

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

    # ── Thresholds ───────────────────────────────────────────────────────────
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

    # ── Carregamento ─────────────────────────────────────────────────────────
    with st.spinner("", show_time=False):
        raw = load_data(tenant_id, revisao_id, ver, st.session_state.get("sb_access_token", ""))

    tarefas = raw["tarefas"]
    revisao = raw["revisao"]

    if not tarefas:
        notice_card("Revisão sem tarefas",
                    "A revisão selecionada ainda não possui tarefas sincronizadas.",
                    tone="warning")
        return

    # ── Filtro de escopo ─────────────────────────────────────────────────────
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

    selection_summary(
        "Parâmetros dos alertas",
        {
            "Revisão":            revisao.get("titulo") or "-",
            "Semana":             f"{alertas.get('semana_atual', 1)}/{alertas.get('semanas_total', 1)}",
            "Travado há":         f"{int(dias_travado)} dia(s)",
            "Sem atualização há": f"{int(dias_sem_update)} dia(s)",
        },
        caption="Os alertas abaixo usam os limites configurados no painel de thresholds.",
    )

    total_alertas = (len(alertas["travados"]) + len(alertas["sem_inicio"])
                     + len(alertas["sem_update"]) + len(alertas["risco_prazo"]))
    if total_alertas == 0:
        notice_card(
            "Nenhum alerta crítico encontrado",
            "Com os parâmetros atuais, a revisão não possui equipamentos em alerta.",
            tone="success",
        )

    # ── Resumo global ─────────────────────────────────────────────────────────
    fragment_resumo(alertas, revisao)
    st.markdown("---")

    # ── Abas ──────────────────────────────────────────────────────────────────
    tab_trav, tab_sem, tab_par, tab_risc, tab_grupos, tab_export, tab_email = st.tabs([
        f"🚫 Travados ({len(alertas['travados'])})",
        f"⬜ Sem início ({len(alertas['sem_inicio'])})",
        f"⏸ Parados ({len(alertas['sem_update'])})",
        f"⚠️ Risco prazo ({len(alertas['risco_prazo'])})",
        "📊 Por grupo",
        "⬇️ Exportar",
        "📧 Enviar por e-mail",
    ])

    with tab_trav:   fragment_travados(alertas["travados"])
    with tab_sem:    fragment_sem_inicio(alertas["sem_inicio"])
    with tab_par:    fragment_parados(alertas["sem_update"])
    with tab_risc:   fragment_risco_prazo(alertas["risco_prazo"])
    with tab_grupos: fragment_resumo_grupos(alertas)

    with tab_export:
        st.markdown("### ⬇️ Exportações")
        st.caption("Baixe os alertas em formato CSV por categoria ou PDF consolidado.")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**CSV por categoria**")
            for df, label, fname in [
                (alertas["travados"],    "Travados",    "alertas_travados_exp.csv"),
                (alertas["sem_inicio"],  "Sem início",  "alertas_sem_inicio_exp.csv"),
                (alertas["sem_update"],  "Parados",     "alertas_parados_exp.csv"),
                (alertas["risco_prazo"], "Risco prazo", "alertas_risco_prazo_exp.csv"),
            ]:
                if not df.empty:
                    cols_pub = [c for c in df.columns if c != "dept_id"]
                    st.download_button(f"⬇️ {label}",
                                       data=df[cols_pub].to_csv(index=False).encode("utf-8"),
                                       file_name=fname, mime="text/csv",
                                       use_container_width=True, key=f"dl_{fname}")
        with col2:
            st.markdown("**PDF consolidado**")
            try:
                import reportlab  # noqa: F401
                pdf_bytes = build_pdf_alertas(alertas, revisao)
                titulo_rev = (revisao.get("titulo") or "revisao").replace("/", "-")
                download_action("⬇️ Baixar PDF completo", data=pdf_bytes,
                                file_name=f"alertas_{titulo_rev}.pdf",
                                mime="application/pdf", key="ntf_pdf_dl", type="primary",
                                help="PDF consolidado com todas as categorias de alerta.")
            except ImportError:
                st.info("Instale `reportlab` no requirements.txt para habilitar exportação em PDF.")

    with tab_email:
        st.markdown("### 📧 Envio de Relatório por E-mail")
        st.caption("Envie manualmente ou configure o agendamento automático por departamento.")
        st.divider()
        fragment_disparo_manual(tenant_id, revisao_id, is_admin,
                                int(dias_travado), int(dias_sem_update))
        st.divider()
        fragment_configurar_agendamento(tenant_id, is_admin)

    # ── Botão de atualizar ────────────────────────────────────────────────────
    if refresh_button("ntf_refresh", label="Atualizar alertas",
                      help="Reprocessa os alertas com base nos filtros atuais."):
        bump_data_version()
        clear_cached_functions(load_data)
        st.rerun()
