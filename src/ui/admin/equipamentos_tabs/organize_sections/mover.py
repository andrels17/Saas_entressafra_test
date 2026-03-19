
import streamlit as st
import pandas as pd
from postgrest.exceptions import APIError

from src.ui.admin_components.layout import admin_divider
from src.ui.admin.equipamentos_helpers import (
    _rerun,
    _load_grupos,
    _audit,
)


def render_mover_section(sb, tenant_id: str) -> None:
    grupo_opts, gid_to_name = _load_grupos(sb, tenant_id)
    group_names = list(grupo_opts.keys())
    st.markdown("### Mover em lote + Edição individual")
    rows = (
        sb.table("equipamentos")
        .select("id, frota, modelo, ano, status, grupo_id")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .order("frota")
        .execute()
        .data
    ) or []

    if not rows:
        st.info("Nenhum equipamento ativo.")
        return

    def gname(gid):
        return gid_to_name.get(gid, "Sem grupo") if gid else "Sem grupo"

    df = pd.DataFrame([{
        "id": r["id"],
        "frota": r.get("frota") or "",
        "modelo": r.get("modelo") or "",
        "ano": r.get("ano"),
        "status": r.get("status") or "",
        "grupo": gname(r.get("grupo_id")),
    } for r in rows])

    st.dataframe(
        df.drop(
            columns=["id"]),
        use_container_width=True,
        hide_index=True)

    st.markdown("#### Selecionar para mover (lote)")
    frota_to_id = {
        f"{
            row.frota} — {
            row.modelo} ({
                row.grupo})": row.id for row in df.itertuples(
                    index=False)}
    selecionados = st.multiselect(
        "Equipamentos",
        list(
            frota_to_id.keys()),
        key="equip_move_multi_classic")

    dest_group = st.selectbox(
        "Mover para",
        group_names,
        key="equip_move_dest_classic")
    dest_gid = grupo_opts[dest_group]

    colA, colB = st.columns(2)
    with colA:
        if st.button(
                "Mover selecionados",
                type="primary",
                use_container_width=True,
                disabled=(
                    len(selecionados) == 0),
                key="equip_move_btn_classic"):
            ids = [frota_to_id[k] for k in selecionados]
            try:
                sb.table("equipamentos").update({"grupo_id": dest_gid}).eq(
                    "tenant_id", tenant_id).in_("id", ids).execute()
                _audit(
                    sb, tenant_id, "move", {
                        "ids": ids, "grupo_id": dest_gid})
                st.success(f"{len(ids)} movido(s) para: {dest_group}")
                _rerun()
            except Exception as e:
                st.error(f"Erro ao mover: {e}")

    with colB:
        if st.button(
                "Remover do grupo",
                use_container_width=True,
                disabled=(
                    len(selecionados) == 0),
                key="equip_move_remove_btn_classic"):
            ids = [frota_to_id[k] for k in selecionados]
            try:
                sb.table("equipamentos").update({"grupo_id": None}).eq(
                    "tenant_id", tenant_id).in_("id", ids).execute()
                _audit(sb, tenant_id, "move", {"ids": ids, "grupo_id": None})
                st.success(f"{len(ids)} removido(s) do grupo.")
                _rerun()
            except Exception as e:
                st.error(f"Erro ao remover: {e}")

    admin_divider()
    st.markdown("#### Edição individual (com soft/hard delete)")
    opts = list(frota_to_id.keys())
    selected_label = st.selectbox(
        "Escolha um equipamento",
        options=opts,
        key="equip_edit_select_classic")
    selected_id = frota_to_id[selected_label]

    current = (
        sb.table("equipamentos")
        .select("id, frota, modelo, ano, status, grupo_id")
        .eq("tenant_id", tenant_id)
        .eq("id", selected_id)
        .single()
        .execute()
        .data
    )

    cur_gid = current.get("grupo_id")
    cur_group_name = gid_to_name.get(
        cur_gid, "Sem grupo") if cur_gid else "Sem grupo"

    with st.form("equip_edit_form_classic", clear_on_submit=False):
        c1, c2 = st.columns([0.5, 0.5])
        with c1:
            frota = st.text_input(
                "Frota",
                value=str(
                    current.get("frota") or "").strip(),
                key="classic_frota")
            modelo = st.text_input(
                "Modelo",
                value=str(
                    current.get("modelo") or "").strip(),
                key="classic_modelo")
        with c2:
            ano_val = int(current.get("ano") or 0) if current.get("ano") else 0
            ano = st.number_input(
                "Ano (0 para vazio)",
                min_value=0,
                max_value=2100,
                value=ano_val,
                step=1,
                key="classic_ano")
            status = st.text_input(
                "Status",
                value=str(
                    current.get("status") or "").strip(),
                key="classic_status")
        grupo_name = st.selectbox("Grupo", options=group_names, index=group_names.index(
            cur_group_name) if cur_group_name in group_names else 0, key="classic_grupo")

        save = st.form_submit_button(
            "Salvar alterações",
            type="primary",
            use_container_width=True)

    if save:
        if not frota.strip():
            st.error("Frota não pode ficar vazia.")
        else:
            payload = {
                "frota": frota.strip(),
                "modelo": (modelo.strip() if modelo is not None else ""),
                "status": (status.strip() if status is not None else ""),
                "grupo_id": grupo_opts.get(grupo_name),
                "ano": int(ano) if ano and int(ano) > 0 else None,
            }
            try:
                sb.table("equipamentos").update(payload).eq(
                    "tenant_id", tenant_id).eq(
                    "id", selected_id).execute()
                _audit(
                    sb, tenant_id, "update", {
                        "changes": payload}, equipamento_id=selected_id)
                st.success("Equipamento atualizado.")
                _rerun()
            except APIError as e:
                try:
                    st.json(e.json())
                except Exception:
                    st.error(str(e))
            except Exception as e:
                st.error(f"Erro ao salvar: {e}")

    st.markdown("##### Apagar (soft/hard)")
    colX, colY = st.columns(2)
    with colX:
        if st.button(
            "Desativar (soft delete)",
            icon=":material/block:",
            use_container_width=True,
                key="classic_soft_delete"):
            try:
                sb.table("equipamentos").update({"ativo": False}).eq(
                    "tenant_id", tenant_id).eq("id", selected_id).execute()
                _audit(
                    sb, tenant_id, "soft_delete", {
                        "ids": [selected_id]}, equipamento_id=selected_id)
                st.success("Equipamento desativado.")
                _rerun()
            except Exception as e:
                st.error(f"Erro ao desativar: {e}")

    with colY:
        confirm = st.checkbox(
            "Confirmo apagar definitivamente",
            value=False,
            key="classic_hard_confirm")
        if st.button(
            "Apagar definitivamente",
            use_container_width=True,
            disabled=not confirm,
                key="classic_hard_delete"):
            try:
                sb.table("equipamentos").delete().eq(
                    "tenant_id", tenant_id).eq(
                    "id", selected_id).execute()
                _audit(
                    sb, tenant_id, "hard_delete", {
                        "ids": [selected_id]}, equipamento_id=selected_id)
                st.success("Equipamento apagado definitivamente.")
                _rerun()
            except Exception as e:
                st.error(f"Erro ao apagar: {e}")

# ======== SUBTAB 4: LIMPEZA (EXCLUSÃO EM MASSA) ========
