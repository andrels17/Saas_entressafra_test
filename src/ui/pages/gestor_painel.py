"""Painel do Gestor — pendências críticas e filtros interativos.

Melhorias Streamlit 1.42+:
  - st.dataframe com selection_mode="single-row" + on_select para abrir
    detalhe inline sem st.rerun()
  - @st.fragment para filtros e alertas em reruns parciais
  - st.metric nativo para KPIs de alertas
  - st.segmented_control para filtro de status (substituindo multiselect)
  - st.status para carregamento granular
  - st.pills para seleção rápida de grupo
  - widget bind para sincronizar filtros com query params
"""
from __future__ import annotations

import streamlit as st
import pandas as pd
from src.ui.core.styles import page_header as _ph
from src.ui.core.confirm_dialog import confirm_dialog
from src.ui.core.empty_state import empty_state
from src.ui.core.error_messages import show_supabase_error
from src.utils.ui_helpers import df_to_xlsx, status_badge
from src.utils.supabase_helpers import sb_for_user, current_tenant_id, current_role
from src.utils.mobile import is_mobile
from src.auth.scope import get_user_scope
from src.auth.roles import Role


STATUS_LABELS = {
    "pendente":     "Pendente",
    "em_andamento": "Em andamento",
    "concluido":    "Concluído",
    "travado":      "Travado",
    "nao_aplica":   "Não aplica",
}

_STATUS_COLORS = {
    "travado":      "red",
    "pendente":     "orange",
    "em_andamento": "blue",
    "concluido":    "green",
    "nao_aplica":   "gray",
}


def _try_rpc(sb, fn_name: str, params: dict):
    try:
        return sb.rpc(fn_name, params).execute().data
    except Exception:
        return None


# ── Fragment: painel de alertas ───────────────────────────────────────────────

@st.fragment
def _fragment_alertas(tenant_id: str, revisao_id: str) -> None:
    """Alertas críticos com métricas nativas — reroda independentemente."""
    st.markdown("### Alertas críticos")

    c1, c2, c3 = st.columns(3)
    with c1: days_trav  = st.number_input("Travado há (dias)",         min_value=1, max_value=60,   value=3,   step=1,  key="alerta_trav_days")
    with c2: days_pend  = st.number_input("Pendente/Andamento há (dias)", min_value=1, max_value=120, value=7,   step=1,  key="alerta_pend_days")
    with c3: lim_alerts = st.number_input("Limite alertas",            min_value=50, max_value=5000, value=300, step=50, key="alerta_limit")

    sb      = sb_for_user()
    alerts  = _try_rpc(sb, "pendencias_criticas", {
        "p_tenant_id":    tenant_id,
        "p_revisao_id":   revisao_id,
        "p_days_travado": int(days_trav),
        "p_days_pendente": int(days_pend),
    })

    if alerts is None:
        st.warning("RPC `pendencias_criticas` não encontrada. "
                   "Rode `sql/etapa7_rpcs_alertas.sql` no Supabase para habilitar.")
        return

    df_a = pd.DataFrame(alerts).head(int(lim_alerts))

    if df_a.empty:
        st.success("Nenhum alerta crítico com os thresholds atuais ✅")
        return

    # Métricas nativas em vez de HTML customizado
    travados      = int((df_a.get("status") == "travado").sum())      if "status" in df_a.columns else 0
    pendentes     = int((df_a.get("status") == "pendente").sum())     if "status" in df_a.columns else 0
    em_andamento  = int((df_a.get("status") == "em_andamento").sum()) if "status" in df_a.columns else 0

    m1, m2, m3, m4 = st.columns(4)
    with m1: st.metric("Total alertas",  len(df_a), help="Registros que ultrapassaram o threshold")
    with m2: st.metric("Travados",       travados,   delta=f">{days_trav}d", delta_color="inverse" if travados else "off")
    with m3: st.metric("Pendentes",      pendentes,  delta=f">{days_pend}d", delta_color="inverse" if pendentes else "off")
    with m4: st.metric("Em andamento",   em_andamento)

    # Tabela com seleção de linha para ver detalhes inline
    evento = st.dataframe(
        df_a,
        use_container_width=True,
        hide_index=True,
        selection_mode="single-row",
        on_select="rerun",
        key="df_alertas",
    )

    if evento.selection.rows:
        idx  = evento.selection.rows[0]
        row  = df_a.iloc[idx]
        with st.expander(f"Detalhes — {row.get('equipamento','') or row.get('Equipamento','')}", expanded=True):
            cols = st.columns(3)
            for i, (k, v) in enumerate(row.items()):
                cols[i % 3].metric(str(k), str(v) if pd.notna(v) else "—")

    st.download_button(
        "Exportar alertas CSV",
        icon=":material/download:",
        data=df_a.to_csv(index=False).encode("utf-8"),
        file_name="alertas_criticos.csv",
        mime="text/csv",
        use_container_width=True,
        key="alerta_csv_btn",
    )


@st.fragment
def _fragment_parados(tenant_id: str, revisao_id: str, dias_sem_mov: int = 7) -> None:
    """Equipamentos sem movimentação recente."""
    st.markdown("### Equipamentos sem movimentação")
    sb = sb_for_user()
    rows = (
        sb.table("tarefas_servico")
        .select(
            "status,semana,updated_at,dt_etapa_d,dt_etapa_r,dt_etapa_m,"
            "equipamentos(id,frota,modelo,equip_grupos(nome,departamento_id))"
        )
        .eq("tenant_id", tenant_id)
        .eq("revisao_id", revisao_id)
        .limit(5000)
        .execute()
        .data
    ) or []

    grouped: dict[str, dict] = {}
    for r in rows:
        eq = r.get("equipamentos") or {}
        eid = eq.get("id")
        if not eid:
            continue
        grp = eq.get("equip_grupos") or {}
        item = grouped.setdefault(eid, {
            "Frota": eq.get("frota") or eid,
            "Modelo": eq.get("modelo") or "",
            "Grupo": grp.get("nome") or "Sem grupo",
            "ultima_semana": None,
            "ultima_mov": None,
            "status_final": r.get("status") or "pendente",
        })
        mov = max([x for x in [r.get("dt_etapa_m"), r.get("dt_etapa_r"), r.get("dt_etapa_d"), r.get("updated_at")] if x] or [None])
        if mov and (item["ultima_mov"] is None or mov > item["ultima_mov"]):
            item["ultima_mov"] = mov
        sem = int(r.get("semana") or 0)
        if any([r.get("dt_etapa_d"), r.get("dt_etapa_r"), r.get("dt_etapa_m")]) and sem > (item["ultima_semana"] or 0):
            item["ultima_semana"] = sem
        item["status_final"] = r.get("status") or item["status_final"]

    from src.utils.timezone import days_since_utc
    out = []
    for item in grouped.values():
        dias = days_since_utc(item.get("ultima_mov"))
        if item.get("status_final") == "concluido":
            continue
        if dias is None or dias < dias_sem_mov:
            continue
        out.append({
            "Frota": item["Frota"],
            "Modelo": item["Modelo"],
            "Grupo": item["Grupo"],
            "Últ. semana": f"Sem. {item['ultima_semana']}" if item.get("ultima_semana") else "Sem registro",
            "Dias sem mov.": dias,
        })

    df = pd.DataFrame(sorted(out, key=lambda x: (-x["Dias sem mov."], str(x["Frota"]))))
    if df.empty:
        st.success("Nenhum equipamento parado no recorte atual ✅")
        return
    m1, m2, m3 = st.columns(3)
    with m1: st.metric("Equipamentos parados", len(df))
    with m2: st.metric("Maior tempo", f"{int(df['Dias sem mov.'].max())} dias")
    with m3: st.metric("Grupos afetados", int(df['Grupo'].nunique()))
    st.dataframe(df, use_container_width=True, hide_index=True)


# ── Fragment: tabela de pendências com filtros ────────────────────────────────

@st.fragment
def _fragment_pendencias(
    tenant_id: str,
    revisao_id: str,
    scope_dept_ids,
    scope_grp_ids,
    grupos: list[dict],
    setores: list[dict],
) -> None:
    """Pendências gerais com filtros reativos em fragment isolado."""
    st.markdown("### Pendências")

    # ── Filtros ───────────────────────────────────────────────────────────────
    grupo_opts  = {"(Todos)": None, **{g["nome"]: g["id"] for g in grupos}}
    setor_opts  = {"(Todos)": None, **{s["nome"]: s["id"] for s in setores}}

    # Bind query_params → default dos selectboxes (#1)
    _grupo_names = list(grupo_opts.keys())
    _setor_names = list(setor_opts.keys())
    _qp_grupo    = st.query_params.get("grp")
    _qp_setor    = st.query_params.get("sec")
    _def_grupo   = _qp_grupo if _qp_grupo in _grupo_names else _grupo_names[0]
    _def_setor   = _qp_setor if _qp_setor in _setor_names else _setor_names[0]

    f1, f2, f3 = st.columns([0.34, 0.33, 0.33])
    with f1:
        grupo_nome = st.selectbox("Grupo", _grupo_names, index=_grupo_names.index(_def_grupo), key="pend_grupo_sel")
    with f2:
        setor_nome = st.selectbox("Setor", _setor_names, index=_setor_names.index(_def_setor), key="pend_setor_sel")
    with f3:
        busca = st.text_input("Buscar equipamento", placeholder="Ex.: 2055, JD 6190…", key="pend_busca")

    # Sincroniza URL com filtros selecionados
    if grupo_nome != "(Todos)": st.query_params["grp"] = grupo_nome
    elif "grp" in st.query_params: del st.query_params["grp"]
    if setor_nome != "(Todos)": st.query_params["sec"] = setor_nome
    elif "sec" in st.query_params: del st.query_params["sec"]

    # segmented_control para status (mais compacto que multiselect no desktop)
    status_opts   = ["travado", "pendente", "em_andamento", "concluido", "nao_aplica"]
    status_labels = [STATUS_LABELS[s] for s in status_opts]

    if is_mobile():
        only_open    = st.checkbox("Somente pendentes", value=True, key="pend_mobile_open")
        default_status = ["travado", "pendente", "em_andamento"] if only_open else ["travado", "pendente"]
        statuses     = st.multiselect("Status", status_opts,
                                       format_func=lambda x: STATUS_LABELS[x],
                                       default=default_status, key="pend_statuses_mobile")
    else:
        sel_labels = st.pills(
            "Status",
            status_labels,
            selection_mode="multi",
            default=["Travado", "Pendente"],
            key="pend_status_pills",
        )
        statuses = [status_opts[status_labels.index(l)] for l in (sel_labels or [])]

    limit = st.number_input("Limite de registros", min_value=50, max_value=2000,
                             value=300, step=50, key="pend_limit")

    # ── Query ─────────────────────────────────────────────────────────────────
    sb = sb_for_user()
    with st.spinner("", show_time=False):
        q = (
            sb.table("tarefas_servico")
            .select(
                "id,status,semana,observacao,updated_at,"
                "servicos(nome,setores(nome)),"
                "equipamentos(id,frota,modelo,equip_grupos(id,nome,departamento_id))"
            )
            .eq("tenant_id", tenant_id)
            .eq("revisao_id", revisao_id)
        )
        if statuses:
            q = q.in_("status", statuses)
        rows = q.order("updated_at", desc=False).limit(int(limit)).execute().data or []

    # ── Transforma ────────────────────────────────────────────────────────────
    out = []
    for r in rows:
        svc     = r.get("servicos") or {}
        setor   = (svc.get("setores") or {}).get("nome") or ""
        eq      = r.get("equipamentos") or {}
        frota   = eq.get("frota") or ""
        modelo  = eq.get("modelo") or ""
        grp_obj = eq.get("equip_grupos") or {}
        grupo   = grp_obj.get("nome") or "Sem grupo"
        grp_id  = grp_obj.get("id")
        dep_id  = grp_obj.get("departamento_id")

        if scope_dept_ids and dep_id and dep_id not in scope_dept_ids: continue
        if scope_grp_ids  and grp_id and grp_id not in scope_grp_ids:  continue

        equipamento = f"{frota} — {modelo}".strip(" —")

        if grupo_opts.get(grupo_nome)   and grupo      != grupo_nome:                                           continue
        if setor_opts.get(setor_nome)   and setor      != setor_nome:                                           continue
        if busca.strip()                and busca.strip().lower() not in equipamento.lower():                   continue

        out.append({
            "_id":         r.get("id"),
            "_status_raw": r.get("status"),
            "Grupo":       grupo,
            "Equipamento": equipamento,
            "Setor":       setor,
            "Serviço":     svc.get("nome") or "",
            "Status":      STATUS_LABELS.get(r.get("status"), r.get("status")),
            "Semana":      r.get("semana") or "",
            "Atualizado":  r.get("updated_at") or "",
            "Observação":  r.get("observacao") or "",
        })

    df = pd.DataFrame(out)
    if df.empty:
        st.info("Nenhuma tarefa para os filtros.")
        return

    df["_prio"] = df["_status_raw"].map({"travado": 0, "pendente": 1, "em_andamento": 2, "concluido": 3, "nao_aplica": 9}).fillna(5)
    df = df.sort_values(["_prio", "Grupo", "Equipamento", "Setor", "Serviço"]).drop(columns=["_prio"])

    # Métricas do recorte atual
    m1, m2, m3 = st.columns(3)
    with m1: st.metric("Total no recorte", len(df))
    with m2: st.metric("Travados",  int((df["_status_raw"] == "travado").sum()),  delta_color="inverse")
    with m3: st.metric("Pendentes", int((df["_status_raw"] == "pendente").sum()), delta_color="inverse")

    df_display = df.drop(columns=["_id", "_status_raw"])

    # ── Mobile: kanban com ações rápidas ──────────────────────────────────────
    if is_mobile():
        weeks  = sorted([int(w) for w in df["Semana"].dropna().unique() if str(w).isdigit()])
        wsel   = st.selectbox("Semana", weeks, index=0, key="pend_wsel") if weeks else None
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Concluir semana", use_container_width=True,
                          disabled=not bool(wsel), key="pend_batch_done"):
                st.session_state["_gp_batch_done_confirm"] = True

        if confirm_dialog(
            trigger_key="_gp_batch_done_confirm",
            title=f"Concluir todas as tarefas da semana {wsel}?",
            body="Esta acao marca como **Concluido** todas as tarefas pendentes/em andamento da semana selecionada.",
            confirm_label="Confirmar",
        ):
            ids = df[(df["Semana"] == wsel) & (df["_status_raw"].isin(["pendente", "em_andamento"]))]["_id"].dropna().tolist()
            if ids:
                try:
                    sb.table("tarefas_servico").update({"status": "concluido"}).in_("id", ids).execute()
                    st.toast(f"{len(ids)} tarefas concluidas.", icon=":material/check_circle:")
                except Exception as e:
                    show_supabase_error(e, "Acao em lote")
                st.rerun()
            else:
                st.info("Nada para concluir nessa semana.")
        with c2:
            trav_reason = st.text_input("Motivo padrão (travado)", placeholder="Ex.: aguardando peça", key="pend_trav_reason")

        for tab_label, stt in zip(["Travados", "Pendentes", "Em andamento"], ["travado", "pendente", "em_andamento"]):
            with st.expander(tab_label, expanded=(stt == "travado")):
                sdf = df[df["_status_raw"] == stt].copy()
                if sdf.empty:
                    st.caption("Nenhum item.")
                    continue
                for _, row in sdf.iterrows():
                    st.markdown(f"**{row['Equipamento']}**")
                    st.caption(f"{row['Grupo']} • {row['Setor']} • Sem. {row['Semana']}")
                    st.caption(row["Serviço"])
                    b1, b2, b3 = st.columns(3)
                    tid = row["_id"]
                    if not tid: continue
                    with b1:
                        if st.button("✅", key=f"done_{tid}", help="Concluir"):
                            sb.table("tarefas_servico").update({"status": "concluido"}).eq("id", tid).execute()
                            st.rerun()
                    with b2:
                        if st.button("🚧", key=f"go_{tid}", help="Em andamento"):
                            sb.table("tarefas_servico").update({"status": "em_andamento"}).eq("id", tid).execute()
                            st.rerun()
                    with b3:
                        if st.button("⛔", key=f"block_{tid}", help="Travado"):
                            payload = {"status": "travado"}
                            if trav_reason.strip():
                                payload["observacao"] = trav_reason.strip()
                            sb.table("tarefas_servico").update(payload).eq("id", tid).execute()
                            st.rerun()
                    st.divider()

    else:
        # Desktop: tabela clicável com detalhe inline
        evento = st.dataframe(
            df_display,
            use_container_width=True,
            hide_index=True,
            selection_mode="single-row",
            on_select="rerun",
            key="df_pendencias",
        )

        if evento.selection.rows:
            idx = evento.selection.rows[0]
            row = df.iloc[idx]
            tid = row["_id"]
            with st.expander(
                f"Ação rápida — {row['Equipamento']} · {row['Serviço']}",
                expanded=True,
            ):
                c1, c2, c3, c4 = st.columns(4)
                with c1:
                    if st.button("✅ Concluir",      key=f"quick_done_{tid}", use_container_width=True):
                        sb.table("tarefas_servico").update({"status": "concluido"}).eq("id", tid).execute()
                        st.cache_data.clear(); st.rerun()
                with c2:
                    if st.button("🚧 Em andamento",  key=f"quick_go_{tid}",   use_container_width=True):
                        sb.table("tarefas_servico").update({"status": "em_andamento"}).eq("id", tid).execute()
                        st.cache_data.clear(); st.rerun()
                with c3:
                    motivo = st.text_input("Motivo", key=f"quick_motivo_{tid}", placeholder="Obrigatório para Travado")
                with c4:
                    if st.button("⛔ Travar",        key=f"quick_block_{tid}", use_container_width=True):
                        if not motivo.strip():
                            st.error("Preencha o motivo antes de travar.")
                        else:
                            sb.table("tarefas_servico").update({"status": "travado", "observacao": motivo}).eq("id", tid).execute()
                            st.cache_data.clear(); st.rerun()

    _pnd1, _pnd2 = st.columns(2)
    with _pnd1:
        st.download_button(
            "⬇ CSV",
            icon=":material/download:",
            data=df_display.to_csv(index=False).encode("utf-8"),
            file_name="pendencias_gestor.csv",
            mime="text/csv",
            use_container_width=True,
            key="pend_csv_btn",
        )
    with _pnd2:
        try:
            st.download_button(
                "⬇ XLSX",
                icon=":material/download:",
                data=df_to_xlsx(df_display, sheet_name="Pendências"),
                file_name="pendencias_gestor.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="pend_xlsx_btn",
            )
        except Exception:
            pass


# ── Ponto de entrada público ──────────────────────────────────────────────────

def render_gestor_painel() -> None:
    _ph("◈", "Painel do Gestor", "Pendências críticas, travados e filtro rápido por grupo/setor/equipamento.")

    role = current_role()
    if role not in Role.MANAGER_ROLES:
        st.error("Apenas Admin/Gestor pode acessar este painel.")
        st.stop()

    tenant_id = current_tenant_id()
    sb        = sb_for_user()
    user_id   = st.session_state.get("sb_user_id") or ""

    scope_dept_ids, scope_grp_ids = get_user_scope(sb, tenant_id, user_id, role=role)

    # Revisão ativa
    with st.spinner("", show_time=False):
        rev = (
            sb.table("revisoes")
            .select("id,titulo,status")
            .eq("tenant_id", tenant_id)
            .eq("status", "ativa")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
            .data
        ) or []

    if not rev:
        empty_state(
            icon="◑", title="Nenhuma revisão ativa",
            description="Ative uma revisão existente ou crie uma nova para ver as pendências.",
            action_label="Ir para Revisões", action_key="gestor_goto_rev",
            nav_to="Admin - Revisões",
        )
        return

    revisao_id = rev[0]["id"]
    status_badge(rev[0].get("status", "ativa"))
    st.caption(f"Revisão: {rev[0]['titulo']}")

    # Pré-carrega grupos e setores (fora dos fragments para compartilhar)
    gq = (
        sb.table("equip_grupos")
        .select("id,nome,departamento_id")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .order("nome")
    )
    if scope_dept_ids: gq = gq.in_("departamento_id", scope_dept_ids)
    if scope_grp_ids:  gq = gq.in_("id", scope_grp_ids)
    grupos  = gq.execute().data or []

    setores = (
        sb.table("setores")
        .select("id,nome")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .order("nome")
        .execute()
        .data
    ) or []

    # Fragment 1: alertas
    _fragment_alertas(tenant_id, revisao_id)

    st.divider()

    # Fragment 2: pendências com filtros reativos
    _fragment_pendencias(tenant_id, revisao_id, scope_dept_ids, scope_grp_ids, grupos, setores)
