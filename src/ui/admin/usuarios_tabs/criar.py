"""Tab 1 — Criar usuário manualmente ou via convite."""
import streamlit as st
from src.auth.audit import audit_user_created

ROLES = ["admin", "supervisor", "gestor", "executor", "viewer"]


def render_tab_criar(svc, tenant_id: str, rerun_fn, safe_json_fn):
    st.markdown("### Criar usuário manualmente")
    st.caption(
        "O admin define a senha (sem link de convite). O usuário já entra ativo no tenant.")

    with st.form("create_user_form", clear_on_submit=False):
        email = st.text_input("Email", placeholder="usuario@empresa.com")
        nome = st.text_input("Nome (opcional)")
        user_role = st.selectbox("Role no tenant", ROLES, index=2)
        col1, col2 = st.columns(2)
        with col1:
            password = st.text_input(
                "Senha",
                type="password",
                placeholder="Mín. 6 caracteres")
        with col2:
            password2 = st.text_input("Confirmar senha", type="password")
        email_confirm = st.checkbox(
            "Marcar e-mail como confirmado", value=True)
        submitted = st.form_submit_button(
            "Criar usuário", type="primary", use_container_width=True)

    if submitted:
        if not email or "@" not in email:
            st.warning("Informe um e-mail válido.")
            st.stop()
        if not password or len(password) < 6:
            st.warning("A senha deve ter pelo menos 6 caracteres.")
            st.stop()
        if password != password2:
            st.warning("As senhas não conferem.")
            st.stop()
        try:
            created = svc.auth.admin.create_user({
                "email": email.strip().lower(),
                "password": password,
                "email_confirm": bool(email_confirm),
            })
            new_user_id = created.user.id
            try:
                svc.table("user_profiles").upsert(
                    {"user_id": new_user_id, "nome": nome}).execute()
            except Exception:
                pass
            svc.table("tenant_users").upsert(
                {"tenant_id": tenant_id, "user_id": new_user_id, "role": user_role}
            ).execute()
            audit_user_created(new_user_id, email.strip().lower(), user_role)
            st.success(f"Usuário criado: {email} (role: {user_role}).")
            rerun_fn()
        except Exception as e:
            st.error("Erro ao criar usuário.")
            st.json(safe_json_fn(e))

    st.divider()
    st.markdown("### (Opcional) Convidar por e-mail")
    st.caption(
        "Mantido por compatibilidade, mas você pode usar só criação manual.")

    with st.form("invite_user_form"):
        email_i = st.text_input(
            "Email do usuário",
            placeholder="usuario@empresa.com",
            key="invite_email")
        nome_i = st.text_input("Nome (opcional)", key="invite_nome")
        role_i = st.selectbox("Role", ROLES, index=2, key="invite_role")
        invite_submit = st.form_submit_button(
            "Enviar convite", use_container_width=True)

    if invite_submit:
        if not email_i:
            st.warning("Informe o email.")
            st.stop()
        try:
            invite = svc.auth.admin.invite_user_by_email(
                email_i.strip().lower())
            invited_user_id = invite.user.id
            try:
                svc.table("user_profiles").upsert(
                    {"user_id": invited_user_id, "nome": nome_i}).execute()
            except Exception:
                pass
            svc.table("tenant_users").upsert(
                {"tenant_id": tenant_id, "user_id": invited_user_id, "role": role_i}
            ).execute()
            audit_user_created(
                invited_user_id,
                email_i.strip().lower(),
                role_i)
            st.success(
                f"Convite enviado para {email_i}. Usuário vinculado como {role_i}.")
            rerun_fn()
        except Exception as e:
            st.error("Erro ao convidar usuário.")
            st.json(safe_json_fn(e))
