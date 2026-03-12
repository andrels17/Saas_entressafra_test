import streamlit as st
from src.db.supabase_client import get_supabase_anon_fresh
from src.auth.session import set_auth_session, reset_for_login_attempt
from src.ui.core.error_messages import show_error
from src.utils import nav


def render_login():
    st.markdown(
        """
        <style>
        section[data-testid="stSidebar"] { display: none !important; }
        .login-card {
            width: 100%; max-width: 420px; margin: 0 auto;
            background: linear-gradient(145deg, #161B22, #0D1117);
            border: 1px solid rgba(220, 38, 38, 0.25);
            border-radius: 20px; padding: 2.8rem 2.6rem 2.2rem;
            box-shadow: 0 0 60px rgba(220,38,38,0.07), 0 20px 60px rgba(0,0,0,0.5);
        }
        .login-logo-icon {
            width: 44px; height: 44px;
            background: linear-gradient(135deg, #DC2626, #7f1d1d);
            border-radius: 12px; display: inline-flex;
            align-items: center; justify-content: center;
            font-size: 1.4rem; box-shadow: 0 4px 14px rgba(220,38,38,0.40);
            margin-bottom: 0.6rem;
        }
        .login-brand { font-size: 1.5rem; font-weight: 800; color: #E6EDF3; letter-spacing: -0.5px; }
        .login-brand span { color: #DC2626; }
        .login-badge {
            display: inline-flex; align-items: center; gap: 5px;
            background: rgba(220,38,38,0.12); border: 1px solid rgba(220,38,38,0.30);
            color: #f87171; font-size: 0.68rem; font-weight: 700;
            padding: 2px 10px; border-radius: 999px; letter-spacing: 0.06em;
            margin-top: 6px; margin-bottom: 1.4rem;
        }
        .login-tagline {
            font-size: 0.83rem; color: #6B7280;
            padding-bottom: 1.6rem; border-bottom: 1px solid rgba(255,255,255,0.06);
            margin-bottom: 1.6rem; line-height: 1.5;
        }
        .login-footer { text-align: center; color: #374151; font-size: 0.72rem; margin-top: 1.4rem; }
        /* Remove borda padrão do form do Streamlit */
        [data-testid="stForm"] {
            border: none !important;
            padding: 0 !important;
            background: transparent !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    _, center, _ = st.columns([1, 1.8, 1])
    with center:
        st.markdown(
            """
            <div class="login-card">
              <div class="login-logo-icon">🌾</div>
              <div class="login-brand">Agro<span>Safra</span></div>
              <div class="login-badge">⬤&nbsp; ENTERPRISE</div>
              <div class="login-tagline">
                Plataforma de gestão de entressafra.<br>Acesse sua conta para continuar.
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

        # st.form garante que email+senha são lidos JUNTOS no submit,
        # independente de colunas ou reruns parciais.
        with st.form("login_form", clear_on_submit=False):
            st.markdown("**E-mail**")
            email = st.text_input(
                "email", placeholder="voce@empresa.com",
                label_visibility="collapsed",
            )
            st.markdown("**Senha**")
            senha = st.text_input(
                "senha", type="password", placeholder="••••••••",
                label_visibility="collapsed",
            )
            st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
            entrar = st.form_submit_button(
                "→  Entrar na plataforma",
                use_container_width=True,
                type="primary",
            )

        # Recuperação de senha (fora do form)
        col_a, col_b = st.columns(2)
        with col_a:
            forgot = st.button("Esqueci minha senha", use_container_width=True)
        with col_b:
            st.markdown(
                "<p style='color:#4B5563;font-size:0.75rem;text-align:right;"
                "padding-top:9px;margin:0'>Atenção a espaços extras</p>",
                unsafe_allow_html=True,
            )

        st.markdown(
            "<div class='login-footer'>© 2025 AgroSafra · Todos os direitos reservados</div>",
            unsafe_allow_html=True,
        )

        # ── Lógica de login ─────────────────────────────────────────────────
        if entrar:
            reset_for_login_attempt()
            email_norm = (email or "").strip().lower()
            senha_val  = (senha or "").strip()

            if not email_norm or not senha_val:
                st.warning("⚠️ Informe seu e-mail e senha para continuar.")
                return

            sb = get_supabase_anon_fresh()
            # Melhor esforço: garante que não exista sessão “presa” no client/server
            try:
                sb.auth.sign_out()
            except Exception:
                pass
            with st.spinner("Autenticando..."):
                try:
                    res = sb.auth.sign_in_with_password(
                        {"email": email_norm, "password": senha_val}
                    )
                    user_id = res.user.id if res and res.user else ""
                    set_auth_session(
                        res.session.access_token,
                        res.session.refresh_token,
                        user_id,
                    )
                    
                    # Evita herdar tenant/role/menu de sessão anterior
                    for k in ("current_tenant_id","current_role","_tenant_user_id","__nav_to","__current_page","__menu","menu"):
                        st.session_state.pop(k, None)
                    st.session_state["_identity_user_id"] = user_id
                    st.success("✅ Login realizado!")
                    # IMPORTANTE: não forçamos reload completo aqui.
                    # Um reload cria uma nova sessão no servidor e perderíamos os
                    # tokens armazenados no session_state, voltando para a tela de login.
                    # O reload total é usado APENAS no logout.
                    nav.goto("Início")

                except Exception as e:
                    show_error(e)

        # ── Recuperação de senha ────────────────────────────────────────────
        if forgot:
            email_norm = (email or "").strip().lower()
            if not email_norm:
                st.warning("Digite seu e-mail no campo acima primeiro.")
            else:
                sb = get_supabase_anon_fresh()
                try:
                    sb.auth.reset_password_for_email(email_norm)
                    st.success("📧 Se o e-mail estiver cadastrado, enviaremos um link.")
                except Exception as e:
                    st.error(f"Erro: `{repr(e)}`")
