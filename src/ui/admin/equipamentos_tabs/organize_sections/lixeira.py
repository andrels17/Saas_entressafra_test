
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


def render_lixeira_section(sb, tenant_id: str):
        grupo_opts, gid_to_name = _load_grupos(sb, tenant_id)
        group_names = list(grupo_opts.keys())
        st.markdown("### Lixeira (equipamentos desativados)")
        st.caption("Aqui ficam equipamentos com `ativo = false`. Você pode **restaurar** ou **apagar definitivamente**.")

        busca = st.text_input("Buscar na lixeira (frota/modelo)", key="eq_trash_busca")
        q = (
            sb.table("equipamentos")
            .select("id, frota, modelo, ano, status, grupo_id")
            .eq("tenant_id", tenant_id)
            .eq("ativo", False)
            .order("frota")
        )
        rows = q.execute().data or []
        if busca.strip():
            b = busca.strip().lower()
            rows = [r for r in rows if (str(r.get("frota","")).lower().find(b) >= 0) or (str(r.get("modelo","")).lower().find(b) >= 0)]

        if not rows:
            st.info("Lixeira vazia.")
        else:
            def gname(gid):
                return gid_to_name.get(gid, "Sem grupo") if gid else "Sem grupo"

            trash_df = pd.DataFrame([{
                "sel": False,
                "id": r["id"],
                "frota": r.get("frota") or "",
                "modelo": r.get("modelo") or "",
                "ano": r.get("ano"),
                "status": r.get("status") or "",
                "grupo": gname(r.get("grupo_id")),
            } for r in rows])

            edited_trash = st.data_editor(
                trash_df,
                use_container_width=True,
                hide_index=True,
                key="eq_trash_editor",
                column_config={
                    "sel": st.column_config.CheckboxColumn("Sel"),
                    "id": st.column_config.TextColumn("ID", disabled=True),
                    "frota": st.column_config.TextColumn("Frota", disabled=True),
                    "modelo": st.column_config.TextColumn("Modelo", disabled=True),
                    "ano": st.column_config.NumberColumn("Ano", disabled=True),
                    "status": st.column_config.TextColumn("Status", disabled=True),
                    "grupo": st.column_config.TextColumn("Grupo", disabled=True),
                },
            )

            sel_ids = edited_trash[edited_trash["sel"] == True]["id"].tolist()

            col1, col2 = st.columns(2)
            with col1:
                if st.button("Restaurar selecionados", type="primary", use_container_width=True, disabled=(len(sel_ids) == 0), key="eq_trash_restore"):
                    try:
                        sb.table("equipamentos").update({"ativo": True}).eq("tenant_id", tenant_id).in_("id", sel_ids).execute()
                        _audit(sb, tenant_id, "restore", {"ids": sel_ids})
                        st.success(f"Restaurados: {len(sel_ids)}")
                        _rerun()
                    except Exception as e:
                        st.error(f"Erro ao restaurar: {e}")

            with col2:
                confirm = st.checkbox("Confirmo apagar definitivamente", value=False, key="eq_trash_hard_confirm")
                if st.button("Apagar definitivamente", use_container_width=True, disabled=(not confirm or len(sel_ids) == 0), key="eq_trash_hard"):
                    try:
                        sb.table("equipamentos").delete().eq("tenant_id", tenant_id).in_("id", sel_ids).execute()
                        _audit(sb, tenant_id, "hard_delete", {"ids": sel_ids})
                        st.success(f"Apagados: {len(sel_ids)}")
                        _rerun()
                    except Exception as e:
                        st.error(f"Erro ao apagar: {e}")

    # ======== SUBTAB 3: mover / edição individual ========
