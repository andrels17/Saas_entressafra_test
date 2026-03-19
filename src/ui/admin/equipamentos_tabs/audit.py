
import streamlit as st
import pandas as pd
from postgrest.exceptions import APIError

from src.ui.admin.equipamentos_helpers import (
    _chunked,
    _load_user_names,
)


def render_audit_tab(sb, tenant_id: str) -> None:
    st.markdown("### Histórico de mudanças (Audit)")
    st.caption(
        "Registra ações como importação, edição, mover, desativar, restaurar e apagar.")

    # Filtros
    c1, c2, c3, c4 = st.columns([0.28, 0.24, 0.24, 0.24])
    with c1:
        busca = st.text_input(
            "Buscar (frota / modelo / usuário)",
            placeholder="2055, John Deere, André...",
            key="eq_audit_search")
    with c2:
        action = st.selectbox(
            "Ação",
            options=[
                "(todas)",
                "import",
                "update",
                "move",
                "soft_delete",
                "restore",
                "hard_delete"],
            key="eq_audit_action")
    with c3:
        limit = st.selectbox(
            "Quantidade",
            options=[
                50,
                100,
                200,
                500,
                1000],
            index=1,
            key="eq_audit_limit")
    with c4:
        mostrar_json = st.toggle(
            "Mostrar detalhes (JSON)",
            value=False,
            key="eq_audit_json")

    st.caption("Dica: para período, use o filtro do Supabase ou eu posso adicionar filtro por data (início/fim) se você quiser).")

    # Carrega auditoria
    try:
        q = (
            sb.table("equip_audit")
            .select("id, tenant_id, equipamento_id, user_id, action, payload, created_at")
            .eq("tenant_id", tenant_id)
            .order("created_at", desc=True)
            .limit(int(limit))
        )
        if action != "(todas)":
            q = q.eq("action", action)

        events = q.execute().data or []
    except APIError as e:
        st.error("Erro ao carregar auditoria de `equip_audit`.")
        try:
            st.json(e.json())
        except Exception:
            st.error(str(e))
        events = []

    if not events:
        st.info("Nenhum evento encontrado.")
    else:
        # Carrega dados do equipamento para exibir frota/modelo
        equip_ids = [e.get("equipamento_id")
                     for e in events if e.get("equipamento_id")]
        equip_map = {}
        if equip_ids:
            try:
                for chunk in _chunked(list(set(equip_ids)), 200):
                    rows = (
                        sb.table("equipamentos")
                        .select("id, frota, modelo")
                        .eq("tenant_id", tenant_id)
                        .in_("id", chunk)
                        .execute()
                        .data
                    ) or []
                    for r in rows:
                        equip_map[r["id"]] = {"frota": r.get(
                            "frota") or "", "modelo": r.get("modelo") or ""}
            except Exception:
                equip_map = {}

        # Nomes de usuários (se existir user_profiles)
        user_ids = list({e.get("user_id") for e in events if e.get("user_id")})
        user_map = _load_user_names(sb, user_ids)

        # Monta dataframe
        rows_out = []
        for e in events:
            eid = e.get("equipamento_id")
            equip = equip_map.get(eid, {}) if eid else {}
            user_id = e.get("user_id")
            user_name = user_map.get(user_id, "") if user_id else ""

            rows_out.append({
                "quando": e.get("created_at"),
                "ação": e.get("action"),
                "frota": equip.get("frota", ""),
                "modelo": equip.get("modelo", ""),
                "usuário": user_name or (str(user_id) if user_id else ""),
                "equipamento_id": eid or "",
                "evento_id": e.get("id"),
                "detalhes": e.get("payload") if mostrar_json else "",
            })

        df = pd.DataFrame(rows_out)

        # filtro de busca (client-side)
        if busca.strip():
            b = busca.strip().lower()

            def _match(row):
                return (str(row.get("frota", "")).lower().find(b) >= 0) or \
                       (str(row.get("modelo", "")).lower().find(b) >= 0) or \
                       (str(row.get("usuário", "")).lower().find(b) >= 0) or \
                       (str(row.get("ação", "")).lower().find(b) >= 0)
            df = df[df.apply(_match, axis=1)]

        st.dataframe(df, use_container_width=True, hide_index=True)

        # Export CSV
        st.download_button(
            "Exportar auditoria (CSV)",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name="equip_audit.csv",
            mime="text/csv",
            use_container_width=True,
        )

        st.markdown("#### Detalhe do evento")
        with st.expander("Abrir detalhes do evento selecionado"):
            if len(df) == 0:
                st.info("Sem eventos para detalhar.")
            else:
                pick = st.selectbox(
                    "Selecione um evento",
                    options=df["evento_id"].tolist(),
                    key="eq_audit_pick")
                event = next((x for x in events if x.get("id") == pick), None)
                if not event:
                    st.info("Evento não encontrado.")
                else:
                    st.caption(f"**Ação:** {event.get('action', '—')}")
                    st.caption(f"**Quando:** {event.get('created_at', '—')}")
                    st.caption(
                        f"**Usuário:** {user_map.get(event.get('user_id'), event.get('user_id', '—'))}")
                    st.caption(
                        f"**Equipamento ID:** {event.get('equipamento_id', '—')}")
                    st.caption("**Payload:**")
                    st.json(event.get("payload") or {})
