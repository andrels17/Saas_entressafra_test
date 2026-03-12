"""Auditoria — histórico de alterações.

Melhorias Streamlit 1.42+:
  - st.dataframe com selection_mode="single-row" para detalhe inline
  - @st.fragment para filtros em reruns parciais
  - st.metric nativo para KPIs de eventos
  - st.pills para filtro de tipo de evento
  - st.status para carregamento granular
  - st.column_config.DatetimeColumn para formatação nativa da coluna Quando
"""
from __future__ import annotations

import streamlit as st
import pandas as pd
from src.ui.core.styles import page_header as _ph
from src.utils.ui_helpers import df_to_xlsx
from src.utils.supabase_helpers import sb_for_user, current_tenant_id, current_role
from src.utils.fetching import fetch_all
from src.auth.scope import get_user_scope
from src.auth.roles import Role


EVENT_LABELS = {
    "tarefa_created": "Criado",
    "tarefa_updated": "Atualizado",
}


# ── Fragment: filtros + tabela de auditoria ───────────────────────────────────

@st.fragment
def _fragment_auditoria(
    tenant_id: str,
    revisao_id: str,
    scope_dept_ids,
    scope_grp_ids,
) -> None:
    """Tabela de auditoria em fragment isolado."""

    # ── Filtros ───────────────────────────────────────────────────────────────
    f1, f2, f3 = st.columns([0.34, 0.33, 0.33])
    with f1:
        evento_selecionado = st.pills(
            "Tipo de evento",
            ["tarefa_created", "tarefa_updated"],
            format_func=lambda x: EVENT_LABELS.get(x, x),
            selection_mode="multi",
            default=["tarefa_created", "tarefa_updated"],
            key="audit_eventos_pills",
        )
    with f2:
        busca_eq    = st.text_input("Buscar equipamento", placeholder="Frota ou modelo…", key="audit_busca_eq")
    with f3:
        busca_setor = st.text_input("Buscar setor",       placeholder="Mecânica, Elétrica…", key="audit_busca_setor")

    c1, c2 = st.columns(2)
    with c1:
        busca_usuario = st.text_input("Buscar usuário", placeholder="Nome ou ID…", key="audit_busca_usuario")
    with c2:
        limit = st.number_input("Limite de registros", min_value=100, max_value=10000,
                                 value=1000, step=100, key="audit_limit")

    # ── Query ─────────────────────────────────────────────────────────────────
    sb = sb_for_user()

    def filters(q):
        q = q.eq("tenant_id", tenant_id).eq("revisao_id", revisao_id)
        if evento_selecionado:
            q = q.in_("evento", evento_selecionado)
        return q.order("created_at", desc=True)

    with st.spinner("", show_time=False):
        rows = fetch_all(
            sb,
            "historico_eventos",
            "id,created_at,evento,user_id,detalhes,"
            "tarefas_servico(id,status,semana,observacao,"
            "equipamentos(frota,modelo,equip_grupos(id,nome,departamento_id)),"
            "servicos(nome,setores(nome)))",
            filters,
            page_size=2000,
            max_rows=int(limit),
        )

    if not rows:
        st.info("Nenhum evento para os filtros.")
        return

    # ── Resolve nomes de usuário ──────────────────────────────────────────────
    user_ids = sorted({r.get("user_id") for r in rows if r.get("user_id")})
    user_name: dict[str, str] = {}
    for i in range(0, len(user_ids), 200):
        chunk = user_ids[i: i + 200]
        profs = (
            sb.table("user_profiles")
            .select("user_id,nome")
            .in_("user_id", chunk)
            .execute()
            .data
        ) or []
        for p in profs:
            user_name[p["user_id"]] = p.get("nome") or ""

    # ── Transforma ────────────────────────────────────────────────────────────
    out = []
    for r in rows:
        t       = r.get("tarefas_servico") or {}
        eq      = t.get("equipamentos") or {}
        grp_obj = eq.get("equip_grupos") or {} if isinstance(eq.get("equip_grupos"), dict) else {}
        grp     = grp_obj.get("nome")
        grp_id  = grp_obj.get("id")
        dep_id  = grp_obj.get("departamento_id")

        if scope_dept_ids and dep_id and dep_id not in scope_dept_ids: continue
        if scope_grp_ids  and grp_id and grp_id not in scope_grp_ids:  continue

        svc     = t.get("servicos") or {}
        setor   = (svc.get("setores") or {}).get("nome") if isinstance(svc.get("setores"), dict) else None
        equip   = f"{eq.get('frota') or ''} — {eq.get('modelo') or ''}".strip(" —")

        if busca_eq.strip()    and busca_eq.strip().lower()    not in equip.lower():            continue
        if busca_setor.strip() and busca_setor.strip().lower() not in (setor or "").lower():    continue

        uid   = r.get("user_id") or ""
        uname = user_name.get(uid) or (uid[:8] if uid else "—")

        if busca_usuario.strip() and busca_usuario.strip().lower() not in uname.lower(): continue

        det = r.get("detalhes") or {}
        old = det.get("old") if isinstance(det, dict) else None
        new = det.get("new") if isinstance(det, dict) else None

        out.append({
            "Quando":      r.get("created_at"),
            "Evento":      EVENT_LABELS.get(r.get("evento"), r.get("evento")),
            "Usuário":     uname,
            "Grupo":       grp or "Sem grupo",
            "Equipamento": equip,
            "Setor":       setor or "—",
            "Serviço":     svc.get("nome") or "—",
            "De":          old.get("status") if isinstance(old, dict) else "",
            "Para":        new.get("status") if isinstance(new, dict) else (t.get("status") or ""),
            "Semana":      t.get("semana") or "",
            "Obs":         t.get("observacao") or "",
        })

    df = pd.DataFrame(out).sort_values("Quando", ascending=False)

    if df.empty:
        st.info("Nenhum evento após aplicar os filtros.")
        return

    # ── Métricas do recorte ───────────────────────────────────────────────────
    m1, m2, m3, m4 = st.columns(4)
    with m1: st.metric("Total eventos",   len(df))
    with m2: st.metric("Criados",         int((df["Evento"] == "Criado").sum()))
    with m3: st.metric("Atualizações",    int((df["Evento"] == "Atualizado").sum()))
    with m4: st.metric("Usuários únicos", int(df["Usuário"].nunique()))

    # ── Tabela com seleção de linha ───────────────────────────────────────────
    evento = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        selection_mode="single-row",
        on_select="rerun",
        key="df_auditoria",
        column_config={
            "Quando": st.column_config.DatetimeColumn(
                "Quando",
                format="DD/MM/YYYY HH:mm",
                timezone="America/Sao_Paulo",
            ),
            "De":    st.column_config.TextColumn("De",    width="small"),
            "Para":  st.column_config.TextColumn("Para",  width="small"),
            "Obs":   st.column_config.TextColumn("Observação", width="medium"),
        },
    )

    # Detalhe da linha selecionada
    if evento.selection.rows:
        idx = evento.selection.rows[0]
        row = df.iloc[idx]
        with st.expander(
            f"Detalhe — {row['Equipamento']} · {row['Serviço']} · {row['Quando']}",
            expanded=True,
        ):
            c1, c2, c3 = st.columns(3)
            for i, (k, v) in enumerate(row.items()):
                c = [c1, c2, c3][i % 3]
                c.metric(str(k), str(v) if pd.notna(v) and v != "" else "—")

    _dl1, _dl2 = st.columns(2)
    with _dl1:
        st.download_button(
            "⬇ CSV",
            icon=":material/download:",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name="auditoria.csv",
            mime="text/csv",
            use_container_width=True,
            key="audit_csv_btn",
        )
    with _dl2:
        try:
            st.download_button(
                "⬇ XLSX",
                icon=":material/download:",
                data=df_to_xlsx(df, sheet_name="Auditoria"),
                file_name="auditoria.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="audit_xlsx_btn",
            )
        except Exception:
            pass
    st.caption("Dica: se aparecer vazio, confirme que você rodou "
               "`sql/etapa5_auditoria_trigger.sql` no Supabase.")


# ── Ponto de entrada público ──────────────────────────────────────────────────

def render_auditoria() -> None:
    _ph("◎", "Auditoria", "Histórico de alterações gerado automaticamente. "
        "Filtre por revisão, evento, equipamento e setor.")

    role = current_role()
    if role not in Role.MANAGER_ROLES:
        st.error("Apenas Admin/Gestor pode acessar auditoria.")
        st.stop()

    tenant_id = current_tenant_id()
    sb        = sb_for_user()
    user_id   = st.session_state.get("sb_user_id") or ""

    scope_dept_ids, scope_grp_ids = get_user_scope(sb, tenant_id, user_id, role=role)

    # Seletor de revisão (fora do fragment para persistir no URL)
    with st.spinner("", show_time=False):
        revisoes = (
            sb.table("revisoes")
            .select("id,titulo,status")
            .eq("tenant_id", tenant_id)
            .order("created_at", desc=True)
            .execute()
            .data
        ) or []

    if not revisoes:
        st.info("Nenhuma revisão criada.")
        return

    default_idx = next((i for i, r in enumerate(revisoes) if r["status"] == "ativa"), 0)
    rev_labels  = [f"{r['titulo']} [{r['status']}]" for r in revisoes]

    # Sincroniza revisão com query param
    qp_rev_id  = st.query_params.get("rev")
    default_from_qp = next(
        (i for i, r in enumerate(revisoes) if r["id"] == qp_rev_id),
        default_idx,
    )
    rev_sel    = st.selectbox("Revisão", rev_labels, index=default_from_qp, key="audit_revisao_sel")
    revisao    = revisoes[rev_labels.index(rev_sel)]
    revisao_id = revisao["id"]
    st.query_params["rev"] = revisao_id  # sincroniza URL

    st.badge(f"Revisão: {revisao['titulo']}", color="green")

    # Fragment: filtros + tabela
    _fragment_auditoria(tenant_id, revisao_id, scope_dept_ids, scope_grp_ids)
