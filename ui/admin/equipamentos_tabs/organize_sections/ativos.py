
import streamlit as st
import pandas as pd
from postgrest.exceptions import APIError

from src.ui.admin_components.layout import admin_block, admin_divider
from src.ui.admin.equipamentos_helpers import (
    _rerun,
    _load_grupos,
    _load_departamentos,
    _load_user_names,
    _audit,
    _chunked,
    _safe_int,
)


def render_ativos_section(sb, tenant_id: str):
        admin_block("Equipamentos ativos", "Edite, selecione e mova ativos em lote.")
        f_col1, f_col2, f_col3 = st.columns([0.4, 0.3, 0.3])
        with f_col1:
            busca = st.text_input("Buscar (frota/modelo)", placeholder="2055, John Deere...", key="eq_inline_busca")
        with f_col2:
            filtro_grupo = st.selectbox("Filtrar por grupo", group_names, key="eq_inline_grupo")
        with f_col3:
            filtro_status = st.text_input("Filtrar por status", placeholder="Parado, Rodando...", key="eq_inline_status")

        q = (
            sb.table("equipamentos")
            .select("id, frota, modelo, ano, status, grupo_id")
            .eq("tenant_id", tenant_id)
            .eq("ativo", True)
            .order("frota")
        )

        if filtro_grupo == "Sem grupo":
            q = q.is_("grupo_id", "null")
        elif grupo_opts.get(filtro_grupo):
            q = q.eq("grupo_id", grupo_opts[filtro_grupo])

        if filtro_status.strip():
            q = q.ilike("status", f"%{filtro_status.strip()}%")

        if busca.strip():
            b = busca.strip()
            q1 = q.ilike("frota", f"%{b}%").execute().data or []
            q2 = q.ilike("modelo", f"%{b}%").execute().data or []
            rows = {r["id"]: r for r in (q1 + q2)}.values()
            rows = list(rows)
        else:
            rows = q.execute().data or []

        if not rows:
            st.info("Nenhum equipamento encontrado para os filtros.")
        else:
            def gname(gid):
                return gid_to_name.get(gid, "Sem grupo") if gid else "Sem grupo"

            base_df = pd.DataFrame([{
                "sel": False,
                "id": r["id"],
                "frota": r.get("frota") or "",
                "modelo": r.get("modelo") or "",
                "ano": r.get("ano"),
                "status": r.get("status") or "",
                "grupo": gname(r.get("grupo_id")),
            } for r in rows])

            st.caption("Edite diretamente na tabela e clique em **Salvar mudanças**. Use **Sel** para ações em lote.")

            edited = st.data_editor(
                base_df,
                use_container_width=True,
                hide_index=True,
                key="eq_inline_editor",
                column_config={
                    "sel": st.column_config.CheckboxColumn("Sel", help="Selecionar para ações em lote"),
                    "id": st.column_config.TextColumn("ID", disabled=True),
                    "frota": st.column_config.TextColumn("Frota", required=True),
                    "modelo": st.column_config.TextColumn("Modelo"),
                    "ano": st.column_config.NumberColumn("Ano", min_value=0, max_value=2100, step=1),
                    "status": st.column_config.TextColumn("Status"),
                    "grupo": st.column_config.SelectboxColumn("Grupo", options=group_names),
                },
            )

            edited["frota"] = edited["frota"].astype(str).str.strip()
            edited["modelo"] = edited["modelo"].astype(str).str.strip()
            edited["status"] = edited["status"].astype(str).str.strip()
            edited["ano"] = pd.to_numeric(edited["ano"], errors="coerce").astype("Int64")

            base = base_df.set_index("id")
            cur = edited.set_index("id")

            changed_ids = []
            updates = {}
            for _id in cur.index:
                if _id not in base.index:
                    continue
                diffs = {}
                for col in ("frota", "modelo", "ano", "status", "grupo"):
                    a = base.loc[_id, col]
                    b = cur.loc[_id, col]
                    if (pd.isna(a) and pd.isna(b)) or (a == b):
                        continue
                    diffs[col] = b
                if diffs:
                    changed_ids.append(_id)
                    updates[_id] = diffs

            selected_ids = list(cur[cur["sel"] == True].index)

            cA, cB, cC = st.columns(3)
            cA.metric("Linhas", len(cur))
            cB.metric("Com mudanças", len(changed_ids))
            cC.metric("Selecionadas", len(selected_ids))

            col1, col2, col3 = st.columns(3)
            with col1:
                if st.button("Salvar mudanças", icon=":material/save:", type="primary", use_container_width=True, disabled=(len(changed_ids) == 0), key="eq_inline_save"):
                    try:
                        for _id in changed_ids:
                            d = updates[_id]
                            payload = {}
                            if "frota" in d:
                                payload["frota"] = str(d["frota"]).strip()
                            if "modelo" in d:
                                payload["modelo"] = str(d["modelo"]).strip()
                            if "status" in d:
                                payload["status"] = str(d["status"]).strip()
                            if "ano" in d:
                                payload["ano"] = int(d["ano"]) if (not pd.isna(d["ano"]) and int(d["ano"]) > 0) else None
                            if "grupo" in d:
                                payload["grupo_id"] = grupo_opts.get(d["grupo"])
                            if payload:
                                sb.table("equipamentos").update(payload).eq("tenant_id", tenant_id).eq("id", _id).execute()
                                _audit(sb, tenant_id, "update", {"changes": payload}, equipamento_id=_id)
                        st.success(f"Atualizado(s): {len(changed_ids)}")
                        _rerun()
                    except APIError as e:
                        try:
                            st.json(e.json())
                        except Exception:
                            st.error(str(e))
                    except Exception as e:
                        st.error(f"Erro ao salvar: {e}")

            with col2:
                if st.button("Desativar selecionados", icon=":material/block:", use_container_width=True, disabled=(len(selected_ids) == 0), key="eq_inline_soft_del"):
                    try:
                        sb.table("equipamentos").update({"ativo": False}).eq("tenant_id", tenant_id).in_("id", selected_ids).execute()
                        _audit(sb, tenant_id, "soft_delete", {"ids": selected_ids})
                        st.success(f"Desativados: {len(selected_ids)}")
                        _rerun()
                    except Exception as e:
                        st.error(f"Erro ao desativar: {e}")

            with col3:
                st.selectbox("Destino do mover", group_names, key="eq_inline_move_dest")
                if st.button("Mover selecionados", use_container_width=True, disabled=(len(selected_ids) == 0), key="eq_inline_move"):
                    dest = st.session_state.get("eq_inline_move_dest", "Sem grupo")
                    dest_gid = grupo_opts.get(dest)
                    try:
                        sb.table("equipamentos").update({"grupo_id": dest_gid}).eq("tenant_id", tenant_id).in_("id", selected_ids).execute()
                        _audit(sb, tenant_id, "move", {"ids": selected_ids, "grupo_id": dest_gid})
                        st.success(f"Movidos: {len(selected_ids)} → {dest}")
                        _rerun()
                    except Exception as e:
                        st.error(f"Erro ao mover: {e}")

    # ======== SUBTAB 2: LIXEIRA ========