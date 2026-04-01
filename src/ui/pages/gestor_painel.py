"""Painel do Gestor — visão operacional de pendências por status.

Estrutura:
  - Métricas resumidas no topo (travados / pendentes / em andamento)
  - Abas por status: Travados | Pendentes | Em andamento
  - Filtros colapsáveis (grupo, setor, busca, semana)
  - Ação rápida inline ao clicar numa linha
  - Exportação CSV/XLSX
"""
from __future__ import annotations

import streamlit as st
import pandas as pd

from src.ui.core.styles import page_header as _ph
from src.ui.core.confirm_dialog import confirm_dialog
from src.ui.core.empty_state import empty_state
from src.ui.core.error_messages import show_supabase_error
from src.ui.components.feedback import notice_card
from src.ui.components.actions import refresh_button
from src.utils.ui_helpers import df_to_xlsx, status_badge
from src.utils.supabase_helpers import (
    sb_for_user, current_tenant_id, current_role, sanitize_user_input
)
from src.utils.mobile import is_mobile
from src.auth.scope import get_user_scope
from src.auth.roles import Role
from src.ui.core.cache import bump_data_version
from src.db.supabase_client import get_supabase_anon


# ── Funções de dados cacheadas ────────────────────────────────────────────────

@st.cache_data(ttl=30, show_spinner=False)
def _load_revisao_ativa(tenant_id: str, _token: str = "") -> dict | None:
    """Revisão ativa do tenant — TTL curto para refletir mudanças rapidamente."""
    sb = get_supabase_anon()
    if _token:
        sb.postgrest.auth(_token)
    rows = (
        sb.table("revisoes")
        .select("id,titulo,status")
        .eq("tenant_id", tenant_id)
        .eq("status", "ativa")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
    ) or []
    return rows[0] if rows else None


@st.cache_data(ttl=120, show_spinner=False)
def _load_grupos_gestor(
    tenant_id: str,
    scope_dept_ids: tuple | None,
    scope_grp_ids: tuple | None,
    _token: str = "",
) -> list[dict]:
    """Grupos ativos para os filtros do painel — TTL 2min."""
    sb = get_supabase_anon()
    if _token:
        sb.postgrest.auth(_token)
    q = (
        sb.table("equip_grupos")
        .select("id,nome,departamento_id")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .order("nome")
    )
    if scope_dept_ids:
        q = q.in_("departamento_id", list(scope_dept_ids))
    if scope_grp_ids is not None:
        q = q.in_("id", list(scope_grp_ids))
    return q.execute().data or []


@st.cache_data(ttl=300, show_spinner=False)
def _load_setores_gestor(tenant_id: str, _token: str = "") -> list[dict]:
    """Setores ativos para os filtros — TTL 5min (raramente mudam)."""
    sb = get_supabase_anon()
    if _token:
        sb.postgrest.auth(_token)
    return (
        sb.table("setores")
        .select("id,nome")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .order("nome")
        .execute()
        .data
    ) or []


# ── Constantes ────────────────────────────────────────────────────────────────

_STATUS_LABELS = {
    "travado":      "Travado",
    "pendente":     "Pendente",
    "em_andamento": "Em andamento",
    "concluido":    "Concluído",
    "nao_aplica":   "Não aplica",
}

_STATUS_PRIORITY = {"travado": 0, "pendente": 1, "em_andamento": 2, "concluido": 3, "nao_aplica": 9}

_TAB_STATUS = {
    "⛔ Travados":      "travado",
    "⏳ Pendentes":     "pendente",
    "🔧 Em andamento":  "em_andamento",
}


# ── Carregamento de dados ──────────────────────────────────────────────────────

def _load_tarefas(
    sb, tenant_id: str, revisao_id: str,
    scope_dept_ids, scope_grp_ids,
    limit: int = 500,
) -> pd.DataFrame:
    """Carrega tarefas não concluídas com join completo."""
    q = (
        sb.table("tarefas_servico")
        .select(
            "id,status,semana,observacao,updated_at,"
            "servicos(nome,setores(nome)),"
            "equipamentos(id,frota,modelo,equip_grupos(id,nome,departamento_id))"
        )
        .eq("tenant_id", tenant_id)
        .eq("revisao_id", revisao_id)
        .in_("status", ["travado", "pendente", "em_andamento"])
        .order("updated_at", desc=False)
        .limit(limit)
    )
    rows = q.execute().data or []

    out = []
    for r in rows:
        svc   = r.get("servicos") or {}
        setor = (svc.get("setores") or {}).get("nome") or "—"
        eq    = r.get("equipamentos") or {}
        grp   = eq.get("equip_grupos") or {}
        grp_id  = grp.get("id")
        dep_id  = grp.get("departamento_id")

        # Filtro de escopo
        if scope_dept_ids and dep_id and dep_id not in scope_dept_ids:
            continue
        if scope_grp_ids is not None and grp_id and grp_id not in scope_grp_ids:
            continue

        frota = eq.get("frota") or ""
        modelo = eq.get("modelo") or ""
        equip = f"{frota} — {modelo}".strip(" —") or frota or "—"

        out.append({
            "_id":        r.get("id"),
            "_status":    r.get("status"),
            "_grupo_id":  grp_id,
            "_dep_id":    dep_id,
            "Grupo":      grp.get("nome") or "Sem grupo",
            "Equipamento": equip,
            "Setor":      setor,
            "Serviço":    svc.get("nome") or "—",
            "Status":     _STATUS_LABELS.get(r.get("status"), r.get("status") or "—"),
            "Semana":     r.get("semana") or "",
            "Atualizado": (r.get("updated_at") or "")[:16].replace("T", " "),
            "Observação": r.get("observacao") or "",
        })

    df = pd.DataFrame(out)
    if not df.empty:
        df["_prio"] = df["_status"].map(_STATUS_PRIORITY).fillna(5)
        df = df.sort_values(["_prio", "Grupo", "Equipamento", "Setor"]).drop(columns=["_prio"])
    return df


# ── Componentes de UI ──────────────────────────────────────────────────────────

def _action_panel(sb, df: pd.DataFrame, tab_status: str) -> None:
    """Painel de ação rápida ao selecionar uma linha (desktop)."""
    evento = st.dataframe(
        df.drop(columns=["_id", "_status", "_grupo_id", "_dep_id"]),
        use_container_width=True,
        hide_index=True,
        selection_mode="single-row",
        on_select="rerun",
        key=f"df_pend_{tab_status}",
        column_config={
            "Atualizado": st.column_config.TextColumn("Atualizado", width="small"),
            "Semana": st.column_config.NumberColumn("Semana", width="small"),
        },
    )

    if not evento.selection.rows:
        return

    idx = evento.selection.rows[0]
    row = df.iloc[idx]
    tid = row["_id"]
    if not tid:
        return

    with st.container(border=True):
        st.markdown(
            f"**{row['Equipamento']}** &nbsp;·&nbsp; {row['Serviço']} "
            f"&nbsp;·&nbsp; Sem. {row['Semana']}",
            unsafe_allow_html=True,
        )
        if row["Observação"]:
            st.caption(f"📝 {row['Observação']}")

        c1, c2, c3, c4 = st.columns([1, 1, 2, 1])
        with c1:
            if st.button("✅ Concluir", key=f"done_{tid}", use_container_width=True, type="primary"):
                sb.table("tarefas_servico").update({"status": "concluido"}).eq("id", tid).execute()
                bump_data_version()
                st.rerun()
        with c2:
            if tab_status != "em_andamento":
                if st.button("🔧 Andamento", key=f"go_{tid}", use_container_width=True):
                    sb.table("tarefas_servico").update({"status": "em_andamento"}).eq("id", tid).execute()
                    bump_data_version()
                    st.rerun()
        with c3:
            motivo = st.text_input(
                "Motivo (obrigatório para travar)",
                key=f"motivo_{tid}",
                placeholder="Ex.: aguardando peça…",
                label_visibility="collapsed",
            )
        with c4:
            if tab_status != "travado":
                if st.button("⛔ Travar", key=f"block_{tid}", use_container_width=True):
                    if not motivo.strip():
                        st.error("Preencha o motivo antes de travar.")
                    else:
                        sb.table("tarefas_servico").update({
                            "status": "travado",
                            "observacao": sanitize_user_input(motivo, max_length=300),
                        }).eq("id", tid).execute()
                        bump_data_version()
                        st.rerun()


def _mobile_cards(sb, df: pd.DataFrame, tab_status: str) -> None:
    """Cards compactos para mobile com ações inline."""
    for _, row in df.iterrows():
        tid = row["_id"]
        with st.container(border=True):
            st.markdown(f"**{row['Equipamento']}**")
            st.caption(f"{row['Grupo']} · {row['Setor']} · Sem. {row['Semana']}")
            st.caption(row["Serviço"])
            if row["Observação"]:
                st.caption(f"📝 {row['Observação']}")

            b1, b2, b3 = st.columns(3)
            with b1:
                if st.button("✅", key=f"m_done_{tid}", help="Concluir", use_container_width=True):
                    sb.table("tarefas_servico").update({"status": "concluido"}).eq("id", tid).execute()
                    bump_data_version()
                    st.rerun()
            with b2:
                if tab_status != "em_andamento":
                    if st.button("🔧", key=f"m_go_{tid}", help="Em andamento", use_container_width=True):
                        sb.table("tarefas_servico").update({"status": "em_andamento"}).eq("id", tid).execute()
                        bump_data_version()
                        st.rerun()
            with b3:
                if tab_status != "travado":
                    if st.button("⛔", key=f"m_block_{tid}", help="Travar", use_container_width=True):
                        st.session_state[f"_block_confirm_{tid}"] = True

            if st.session_state.get(f"_block_confirm_{tid}"):
                motivo = st.text_input(
                    "Motivo", key=f"m_motivo_{tid}", placeholder="Por que está travado?")
                if st.button("Confirmar travamento", key=f"m_block_ok_{tid}", type="primary"):
                    if not motivo.strip():
                        st.error("Informe o motivo.")
                    else:
                        sb.table("tarefas_servico").update({
                            "status": "travado",
                            "observacao": sanitize_user_input(motivo, max_length=300),
                        }).eq("id", tid).execute()
                        st.session_state.pop(f"_block_confirm_{tid}", None)
                        bump_data_version()
                        st.rerun()


# ── Fragment principal ─────────────────────────────────────────────────────────

_GESTOR_AUTO_REFRESH_EVERY = "15s"

@st.fragment(run_every=_GESTOR_AUTO_REFRESH_EVERY)
def _fragment_painel(
    tenant_id: str,
    revisao_id: str,
    scope_dept_ids,
    scope_grp_ids,
    grupos: list[dict],
    setores: list[dict],
) -> None:
    sb = sb_for_user()

    # ── Filtros colapsáveis ────────────────────────────────────────────────
    with st.expander("🔍 Filtros", expanded=False):
        fc1, fc2, fc3 = st.columns([1.2, 1.2, 1.6])
        with fc1:
            grupo_opts = {"(Todos os grupos)": None, **{g["nome"]: g["id"] for g in grupos}}
            grupo_sel = st.selectbox(
                "Grupo", list(grupo_opts.keys()), key="gp_fil_grupo")
        with fc2:
            setor_opts = {"(Todos os setores)": None, **{s["nome"]: s["id"] for s in setores}}
            setor_sel = st.selectbox(
                "Setor", list(setor_opts.keys()), key="gp_fil_setor")
        with fc3:
            busca = st.text_input(
                "Buscar equipamento",
                placeholder="Frota ou modelo…",
                key="gp_fil_busca",
            )
        limite = st.slider(
            "Máximo de registros por aba", min_value=50, max_value=1000,
            value=300, step=50, key="gp_fil_limit",
        )

    # ── Carrega dados ──────────────────────────────────────────────────────
    with st.spinner("Carregando pendências…", show_time=False):
        df_all = _load_tarefas(sb, tenant_id, revisao_id, scope_dept_ids, scope_grp_ids, limite)

    if df_all.empty:
        notice_card(
            "Tudo em dia! ✅",
            "Nenhuma tarefa travada, pendente ou em andamento para esta revisão.",
            tone="success",
        )
        return

    # ── Aplica filtros de texto ────────────────────────────────────────────
    df_filtered = df_all.copy()
    if grupo_opts.get(grupo_sel):
        df_filtered = df_filtered[df_filtered["Grupo"] == grupo_sel]
    if setor_opts.get(setor_sel):
        df_filtered = df_filtered[df_filtered["Setor"] == setor_sel]
    if busca.strip():
        bl = busca.strip().lower()
        df_filtered = df_filtered[df_filtered["Equipamento"].str.lower().str.contains(bl, na=False)]

    # ── Métricas resumidas ─────────────────────────────────────────────────
    n_trav = int((df_filtered["_status"] == "travado").sum())
    n_pend = int((df_filtered["_status"] == "pendente").sum())
    n_and  = int((df_filtered["_status"] == "em_andamento").sum())
    total  = n_trav + n_pend + n_and

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total pendente", total)
    m2.metric("⛔ Travados",    n_trav, delta_color="inverse" if n_trav else "off")
    m3.metric("⏳ Pendentes",  n_pend, delta_color="inverse" if n_pend else "off")
    m4.metric("🔧 Em andamento", n_and)

    st.divider()

    # ── Abas por status ────────────────────────────────────────────────────
    tabs = st.tabs(list(_TAB_STATUS.keys()))

    for tab, (tab_label, tab_status) in zip(tabs, _TAB_STATUS.items()):
        with tab:
            df_tab = df_filtered[df_filtered["_status"] == tab_status].copy()

            if df_tab.empty:
                st.info(f"Nenhuma tarefa {tab_label.split()[-1].lower()} com os filtros atuais.")
                continue

            st.caption(f"{len(df_tab)} tarefa(s)")

            if is_mobile():
                _mobile_cards(sb, df_tab, tab_status)
            else:
                _action_panel(sb, df_tab, tab_status)

    # ── Exportação ─────────────────────────────────────────────────────────
    st.divider()
    df_exp = df_filtered.drop(columns=["_id", "_status", "_grupo_id", "_dep_id"])
    col_csv, col_xlsx = st.columns(2)
    with col_csv:
        st.download_button(
            "⬇ Exportar CSV",
            icon=":material/download:",
            data=df_exp.to_csv(index=False).encode("utf-8"),
            file_name="pendencias_gestor.csv",
            mime="text/csv",
            use_container_width=True,
            key="gp_exp_csv",
        )
    with col_xlsx:
        try:
            st.download_button(
                "⬇ Exportar XLSX",
                icon=":material/download:",
                data=df_to_xlsx(df_exp, sheet_name="Pendências"),
                file_name="pendencias_gestor.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="gp_exp_xlsx",
            )
        except Exception:
            pass


# ── Entry point ────────────────────────────────────────────────────────────────



def render_gestor_painel() -> None:
    _ph("◈", "Painel do Gestor",
        "Visão operacional de pendências críticas — filtre, aja e exporte.")
    st.caption(f"Atualização automática ativa a cada {_GESTOR_AUTO_REFRESH_EVERY}.")

    role = current_role()
    if role not in Role.MANAGER_ROLES:
        st.error("Apenas Admin/Gestor pode acessar este painel.")
        st.stop()

    tenant_id = current_tenant_id()
    sb = sb_for_user()
    user_id = st.session_state.get("sb_user_id") or ""
    token   = st.session_state.get("sb_access_token", "")

    import hashlib as _hl
    _tok_hash = _hl.md5(token.encode()).hexdigest()[:8]
    scope_dept_ids, scope_grp_ids = get_user_scope(tenant_id, user_id, role=role, token_hash=_tok_hash)

    # Revisão ativa — cacheada TTL=30s
    rev = _load_revisao_ativa(tenant_id, _token=token)

    if not rev:
        empty_state(
            icon="◑",
            title="Nenhuma revisão ativa",
            description="Ative uma revisão existente ou crie uma nova para ver as pendências.",
            action_label="Ir para Revisões",
            action_key="gestor_goto_rev",
            nav_to="Admin - Revisões",
        )
        return

    revisao_id = rev["id"]

    # Cabeçalho da revisão + botão atualizar
    h1, h2 = st.columns([4, 1])
    with h1:
        status_badge(rev.get("status", "ativa"))
        st.caption(f"Revisão ativa: **{rev['titulo']}**")
    with h2:
        if refresh_button("gp_refresh", help="Recarrega as pendências"):
            bump_data_version()
            st.rerun()

    # Grupos e setores — cacheados TTL=120s e 300s
    scope_dept_tuple = tuple(scope_dept_ids) if scope_dept_ids else None
    scope_grp_tuple  = tuple(scope_grp_ids)  if scope_grp_ids is not None else None
    grupos  = _load_grupos_gestor(tenant_id, scope_dept_tuple, scope_grp_tuple, _token=token)
    setores = _load_setores_gestor(tenant_id, _token=token)

    _fragment_painel(tenant_id, revisao_id, scope_dept_ids, scope_grp_ids, grupos, setores)
