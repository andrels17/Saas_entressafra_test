import streamlit as st
from src.db.supabase_client import get_supabase_service
from src.utils.supabase_helpers import current_user_id
from src.utils import nav


def render_setup_tenant():
    st.markdown("## Configuração inicial")
    st.caption("Crie sua primeira empresa (tenant) e vincule seu usuário como Admin. Isso é feito via Service Role (server-side).")

    user_id = current_user_id()
    if not user_id:
        st.error("Não foi possível identificar seu usuário autenticado. Faça logout/login e tente novamente.")
        st.stop()

    with st.form("setup_tenant_form"):
        nome = st.text_input("Nome da empresa", placeholder="Ex.: Usina Central")
        submitted = st.form_submit_button("Criar empresa e continuar", type="primary", use_container_width=True)

    if not submitted:
        st.info("Após criar a empresa, você poderá cadastrar setores, grupos, equipamentos e revisões.")
        return

    nome = (nome or "").strip()
    if not nome:
        st.warning("Informe o nome da empresa.")
        st.stop()

    svc = get_supabase_service()
    try:
        # 1) criar tenant
        tenant = svc.table("tenants").insert({"nome": nome}).execute().data
        tenant_id = tenant[0]["id"]

        # 2) vincular usuário como admin
        svc.table("tenant_users").upsert({
            "tenant_id": tenant_id,
            "user_id": user_id,
            "role": "admin"
        }).execute()

        st.success("Empresa criada e usuário vinculado como Admin.")
        # set in session and rerun is handled by tenant selection flow
        st.session_state["current_tenant_id"] = tenant_id
        st.session_state["current_role"] = "admin"
        nav.rerun_keep_menu()
    except Exception as e:
        st.error(f"Erro ao criar tenant: {e}")