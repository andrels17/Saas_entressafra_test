import streamlit as st


def is_mobile() -> bool:
    """Retorna True quando o modo mobile estiver ativo.

    Streamlit não expõe user-agent de forma confiável. Então suportamos:
    - toggle na UI (session_state)
    - query param ?mobile=1 (útil para atalhos no chão de fábrica)
    """
    try:
        qp = st.query_params
        qp_mobile = str(
            qp.get(
                "mobile",
                "")).lower() in (
            "1",
            "true",
            "yes",
            "on")
    except Exception:
        qp_mobile = False
    return bool(st.session_state.get("__force_mobile__", False) or qp_mobile)


def render_mobile_toggle() -> None:
    """Toggle do modo mobile.

    Nota: em mobile, a sidebar pode estar escondida por CSS.
    Então também exibimos o toggle no corpo da página quando necessário.
    """
    # Importante: em alguns navegadores (ex.: Safari iOS) a sidebar pode ficar
    # escondida por CSS quando o modo mobile está ativo — e aí o usuário não
    # consegue “voltar” para o desktop.
    #
    # Estratégia:
    # 1) Mantém o checkbox na sidebar (desktop)
    # 2) Renderiza SEMPRE um controle compacto no corpo (desktop e mobile)
    def _sync_mobile_from(widget_key: str):
        """Sincroniza o estado global __force_mobile__ a partir de um widget."""
        st.session_state["__force_mobile__"] = bool(
            st.session_state.get(widget_key, False))

    # Sidebar (desktop)
    try:
        with st.sidebar:
            st.checkbox(
                "📱 Modo chão de fábrica",
                key="__force_mobile_sidebar__",
                value=bool(
                    st.session_state.get(
                        "__force_mobile__",
                        False)),
                on_change=_sync_mobile_from,
                args=(
                    "__force_mobile_sidebar__",
                ),
                help="Ativa a experiência otimizada para celular. Você também pode usar ?mobile=1 no link.",
            )
    except Exception:
        pass  # ignorado — operação opcional

    # Controle no corpo (sempre visível)
    # Usamos checkbox (mais estável que toggle em Safari)
    cols = st.columns([0.58, 0.22, 0.20], vertical_alignment="center")
    with cols[2]:
        st.checkbox(
            "📱 Mobile",
            key="__force_mobile_body__",
            value=bool(st.session_state.get("__force_mobile__", False)),
            on_change=_sync_mobile_from,
            args=("__force_mobile_body__",),
            help="Ative para navegação e controles otimizados para celular. Obs.: ?mobile=1 no link força o mobile.",
        )

    # Logout no corpo: apenas em mobile (evita redundância com a sidebar no
    # desktop)
    with cols[1]:
        if is_mobile() and st.session_state.get("sb_access_token"):
            if st.button(
                "⎋ Sair",
                key="body_logout",
                    use_container_width=True):
                try:
                    from src.auth.session import hard_logout
                    hard_logout()
                except Exception:
                    st.session_state.clear()
                    st.rerun()
