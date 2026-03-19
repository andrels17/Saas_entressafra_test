import streamlit as st
from src.utils import nav
from src.utils.supabase_helpers import sb_for_user, current_tenant_id, current_role

from src.ui.core.styles import page_header as _ph


def render_admin_setores_servicos():
    _ph("◧", "Setores & Serviços",
        "Cadastre setores e serviços — alimentam os Templates e a Matriz.")

    role = current_role()
    if role not in ("admin", "superadmin"):
        st.error("Apenas Admin pode gerenciar setores e serviços.")
        st.stop()

    tenant_id = current_tenant_id()
    sb = sb_for_user()

    tab1, tab2 = st.tabs(["Setores", "Serviços"])

    # ---------------- SETORES ----------------
    with tab1:
        st.markdown("### Setores")

        with st.form("create_setor"):
            nome = st.text_input("Novo setor", placeholder="Ex.: Mecânica")
            submitted = st.form_submit_button(
                "Criar setor", use_container_width=True)

        if submitted:
            nome = (nome or "").strip()
            if not nome:
                st.warning("Informe um nome.")
                st.stop()
            try:
                sb.table("setores").insert(
                    {"tenant_id": tenant_id, "nome": nome}).execute()
                st.success("Setor criado.")
                nav.rerun_keep_menu()
            except Exception as e:
                st.error(f"Erro ao criar setor: {e}")

        only_active = st.toggle(
            "Mostrar apenas ativos",
            value=True,
            key="setores_only_active")
        q = sb.table("setores").select("id,nome,ativo").eq(
            "tenant_id", tenant_id).order("nome")
        if only_active:
            q = q.eq("ativo", True)
        setores = q.execute().data or []

        if not setores:
            st.info("Nenhum setor cadastrado.")
        else:
            for s in setores:
                c1, c2, c3 = st.columns([0.45, 0.35, 0.20])
                with c1:
                    st.markdown(f"**{s['nome']}**")
                    st.caption(f"Ativo: {'Sim' if s['ativo'] else 'Não'}")
                with c2:
                    novo_nome = st.text_input(
                        "Renomear",
                        value=s["nome"],
                        key=f"setor_rename_{
                            s['id']}")
                with c3:
                    colA, colB = st.columns(2)
                    with colA:
                        if st.button(
                                "Salvar",
                                icon=":material/save:",
                                key=f"setor_save_{
                                    s['id']}",
                                use_container_width=True):
                            nn = (novo_nome or "").strip()
                            if not nn:
                                st.warning("Nome inválido.")
                                st.stop()
                            try:
                                sb.table("setores").update({"nome": nn}).eq(
                                    "id", s["id"]).execute()
                                st.toast(
                                    "✓ Atualizado", icon=":material/check_circle:")
                                nav.rerun_keep_menu()
                            except Exception as e:
                                st.error(f"Erro ao salvar: {e}")
                    with colB:
                        label = "Desativar" if s["ativo"] else "Ativar"
                        if st.button(
                            label, key=f"setor_toggle_{
                                s['id']}", use_container_width=True):
                            try:
                                sb.table("setores").update({"ativo": (not s["ativo"])}).eq(
                                    "id", s["id"]).execute()
                                st.toast(
                                    "✓ Ok", icon=":material/check_circle:")
                                nav.rerun_keep_menu()
                            except Exception as e:
                                st.error(f"Erro: {e}")

                st.markdown(
                    "<div style='height:6px'></div>",
                    unsafe_allow_html=True)

    # ---------------- SERVIÇOS ----------------
    with tab2:
        st.markdown("### Serviços")

        setores_all = (
            sb.table("setores")
            .select("id,nome,ativo")
            .eq("tenant_id", tenant_id)
            .order("nome")
            .execute()
            .data
        ) or []

        setores_ativos = [s for s in setores_all if s["ativo"]]
        if not setores_ativos:
            st.warning(
                "Cadastre e ative pelo menos um setor antes de criar serviços.")
            return

        setor_map = {s["nome"]: s["id"] for s in setores_ativos}

        with st.form("create_servico"):
            setor_nome = st.selectbox("Setor", list(setor_map.keys()))
            nome_serv = st.text_input("Novo serviço", placeholder="Ex.: Motor")
            submitted = st.form_submit_button(
                "Criar serviço", use_container_width=True)

        if submitted:
            nome_serv = (nome_serv or "").strip()
            if not nome_serv:
                st.warning("Informe um nome.")
                st.stop()
            try:
                sb.table("servicos").insert({
                    "tenant_id": tenant_id,
                    "setor_id": setor_map[setor_nome],
                    "nome": nome_serv
                }).execute()
                st.success("Serviço criado.")
                nav.rerun_keep_menu()
            except Exception as e:
                st.error(f"Erro ao criar serviço: {e}")

        st.divider()

        filtro_setor = st.selectbox(
            "Filtrar por setor", ["(Todos)"] + list({s["nome"] for s in setores_all}))
        only_active_s = st.toggle(
            "Mostrar apenas ativos",
            value=True,
            key="servicos_only_active")

        q = (
            sb.table("servicos")
            .select("id,nome,ativo,setor_id,setores(nome)")
            .eq("tenant_id", tenant_id)
            .order("nome")
        )
        if only_active_s:
            q = q.eq("ativo", True)

        servicos = q.execute().data or []
        if filtro_setor != "(Todos)":
            servicos = [
                sv for sv in servicos if (
                    sv.get("setores") or {}).get("nome") == filtro_setor]

        if not servicos:
            st.info("Nenhum serviço cadastrado para os filtros.")
            return

        for sv in servicos:
            setor_nome_sv = (sv.get("setores") or {}).get("nome") or "—"
            c1, c2, c3 = st.columns([0.45, 0.35, 0.20])
            with c1:
                st.markdown(f"**{sv['nome']}**")
                st.caption(
                    f"Setor: {setor_nome_sv} • Ativo: {
                        'Sim' if sv['ativo'] else 'Não'}")
            with c2:
                novo_nome = st.text_input(
                    "Renomear",
                    value=sv["nome"],
                    key=f"serv_rename_{
                        sv['id']}")
            with c3:
                colA, colB = st.columns(2)
                with colA:
                    if st.button(
                            "Salvar",
                            icon=":material/save:",
                            key=f"serv_save_{
                                sv['id']}",
                            use_container_width=True):
                        nn = (novo_nome or "").strip()
                        if not nn:
                            st.warning("Nome inválido.")
                            st.stop()
                        try:
                            sb.table("servicos").update({"nome": nn}).eq(
                                "id", sv["id"]).execute()
                            st.toast(
                                "✓ Atualizado", icon=":material/check_circle:")
                            nav.rerun_keep_menu()
                        except Exception as e:
                            st.error(f"Erro ao salvar: {e}")
                with colB:
                    label = "Desativar" if sv["ativo"] else "Ativar"
                    if st.button(
                            label,
                            key=f"serv_toggle_{
                                sv['id']}",
                            use_container_width=True):
                        try:
                            sb.table("servicos").update({"ativo": (not sv["ativo"])}).eq(
                                "id", sv["id"]).execute()
                            st.toast("✓ Ok", icon=":material/check_circle:")
                            nav.rerun_keep_menu()
                        except Exception as e:
                            st.error(f"Erro: {e}")

            st.markdown(
                "<div style='height:6px'></div>",
                unsafe_allow_html=True)
