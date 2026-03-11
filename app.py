import sys
from pathlib import Path

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

import streamlit as st

# ── Core UI ───────────────────────────────────────────────────────────────────
from src.ui.core.styles import inject_global_css, inject_mobile_css, page_header
from src.ui.core.sidebar_display import get_display_names, role_label
from src.ui.core.login import render_login
from src.ui.core.setup_wizard import render_setup_wizard
from src.ui.core.page_registry import PageKey, PAGES, NAV_CONFIG, get_pages_for_role, get_menu_pages

# ── Auth ──────────────────────────────────────────────────────────────────────
from src.auth.roles import Role
from src.auth.guard import require_login, require_role, require_tenant_selected
from src.auth.tenant import ensure_tenant_selected, refresh_current_role
from src.auth.session import clear_auth_session, ensure_valid_token, hard_logout

# ── Utils ─────────────────────────────────────────────────────────────────────
from src.utils.mobile import is_mobile, render_mobile_toggle
from src.utils.supabase_helpers import sb_for_user
from src.auth.scope import get_user_scope

# ── Pages ─────────────────────────────────────────────────────────────────────
from src.ui.pages.home_overview import render_home_overview
from src.ui.pages.dashboard import render_dashboard
from src.ui.pages.gestor_painel import render_gestor_painel
from src.ui.pages.auditoria import render_auditoria
from src.ui.pages.apontamento import render_apontamento
from src.ui.pages.matriz import render_matriz
from src.ui.pages.notificacoes import render_notificacoes

# ── Admin ─────────────────────────────────────────────────────────────────────
from src.ui.admin.usuarios import render_admin_usuarios
from src.ui.admin.departamentos import render_admin_departamentos
from src.ui.admin.grupos import render_admin_grupos
from src.ui.admin.equipamentos import render_admin_equipamentos
from src.ui.admin.integridade import render_admin_integridade
from src.ui.admin.setores_servicos import render_admin_setores_servicos
from src.ui.admin.templates import render_admin_templates
from src.ui.admin.revisoes import render_admin_revisoes
from src.ui.admin.branding_reports import render_admin_branding_reports


st.set_page_config(
    page_title=st.secrets.get("APP_NAME", "AgroSafra"),
    page_icon="🌾",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_global_css()


# ── CSS da sidebar carregado do arquivo .css (sem inline Python) ──────────────
def _inject_sidebar_css():
    from pathlib import Path
    css_path = Path(__file__).parent / "src" / "ui" / "core" / "sidebar.css"
    st.markdown(f"<style>{css_path.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


# ── Sidebar ───────────────────────────────────────────────────────────────────
def _render_sidebar(pages: list[str], current_page: str, role: str, user_id: str, tenant_id: str, mobile: bool) -> str:
    """Sidebar com navegação estável e seção administrativa colapsável."""
    selected = current_page

    with st.sidebar:
        _inject_sidebar_css()

        app_name       = st.secrets.get("APP_NAME", "AgroSafra")
        tname, uname   = get_display_names(tenant_id, user_id)
        rlabel         = role_label(role)
        rnorm          = (role or "").strip().lower()
        rcls           = {
            "admin":      "role-admin",
            "superadmin": "role-superadmin",
            "gestor":     "role-gestor",
        }.get(rnorm, "role-user")
        avatar = (uname[:1] or "U").upper()

        # ── Logo clicável (#4) ── usa st.image(link=) se existir URL de logo do tenant
        logo_url = st.session_state.get("tenant_logo_url")
        if logo_url:
            st.image(logo_url, width=140, use_container_width=False)
            # Botão transparente para navegar ao Início ao clicar na logo
            if st.button("🌾 " + app_name, key="sb_logo_home_btn", use_container_width=True, type="tertiary"):
                st.session_state["__nav_to"] = "Início"
                st.rerun()
        else:
            st.markdown(
                f"""
                <div class="sb-top">
                  <div class="sb-approw">
                    <div class="sb-app">
                      <span class="sb-app-ico">🌾</span>
                      <span class="sb-app-name" title="{app_name}">{app_name}</span>
                    </div>
                    <span class="sb-pill">SaaS</span>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        st.markdown(
            f"""
            <div class="sb-userrow" style="margin-top:6px">
              <div class="sb-avatar">{avatar}</div>
              <div class="sb-usertext">
                <div class="sb-username" title="{uname}">{uname}</div>
                <div class="sb-meta-row">
                  <div class="sb-tenant" title="{tname}">{tname}</div>
                  <span class="role-chip {rcls}">{rlabel}</span>
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        current_label = NAV_CONFIG.get(current_page, ("", "core", current_page))[2]
        st.markdown(f'<div class="sb-current">Página atual: <strong>{current_label}</strong></div>', unsafe_allow_html=True)

        if not mobile:
            st.markdown(
                '<div class="sb-footer"><div class="sb-footer-title">Sessão</div>'
                '<div class="sb-footer-text">Navegue pelo menu lateral e finalize sua sessão com segurança ao sair.</div></div>',
                unsafe_allow_html=True,
            )
            if st.button("Sair", icon=":material/logout:", key="sidebar_logout", use_container_width=True, type="tertiary"):
                hard_logout()

        core_pages  = [p for p in pages if NAV_CONFIG.get(p, ("", "core", ""))[1] == "core"]
        admin_pages = [p for p in pages if NAV_CONFIG.get(p, ("", "core", ""))[1] == "admin"]

        def _fmt(page: str) -> str:
            icon, _, label = NAV_CONFIG.get(page, ("•", "core", page))
            return f"{icon}  {label}"

        def _nav_button(page: str, prefix: str) -> bool:
            return st.button(
                _fmt(page), key=f"{prefix}_{page}",
                use_container_width=True,
                type="primary" if selected == page else "secondary",
            )

        if core_pages:
            st.markdown('<div class="nav-section-label">Principal</div>', unsafe_allow_html=True)
            for page in core_pages:
                if _nav_button(page, "nav_core_btn"):
                    selected = page

        if admin_pages:
            admin_open_key = "_sidebar_admin_open"
            st.session_state.setdefault(admin_open_key, current_page in admin_pages)
            if current_page in admin_pages:
                st.session_state[admin_open_key] = True

            admin_open  = bool(st.session_state.get(admin_open_key, False))
            caret       = "▾" if admin_open else "▸"
            shell_class = "admin-shell open" if admin_open else "admin-shell"

            st.markdown('<div class="nav-section-label">Administração</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="{shell_class}">', unsafe_allow_html=True)
            if st.button(f"{caret}  Administração", key="admin_toggle_btn", use_container_width=True, type="secondary"):
                st.session_state[admin_open_key] = not admin_open
                admin_open = not admin_open
            if admin_open:
                st.markdown('<div class="admin-note">Configurações, cadastros e manutenção do tenant.</div>', unsafe_allow_html=True)
                for page in admin_pages:
                    if _nav_button(page, "nav_admin_btn"):
                        selected = page
            st.markdown('</div>', unsafe_allow_html=True)

    return selected


# ── Guards de identidade e scope ──────────────────────────────────────────────
def _handle_identity_guard(current_uid: str) -> None:
    """Limpa estado derivado se o usuário mudou na mesma sessão do servidor."""
    prev_uid = st.session_state.get("_identity_user_id") or ""
    if prev_uid and current_uid and prev_uid != current_uid:
        try:
            from src.auth.session import clear_derived_state
            clear_derived_state()
        except Exception:
            pass
        for fn in [st.cache_data.clear, st.cache_resource.clear]:
            try: fn()
            except Exception: pass
    st.session_state["_identity_user_id"] = current_uid


def _resolve_scope(tenant_id: str, user_id: str, role: str) -> None:
    """Obtém e armazena o escopo (dept/grupos) do usuário na sessão."""
    sig      = f"{user_id}:{tenant_id}:{role}"
    prev_sig = st.session_state.get("_scope_signature")
    if prev_sig and prev_sig != sig:
        for k in ("__current_page", "__nav_to", "__menu", "menu"):
            st.session_state.pop(k, None)
    st.session_state["_scope_signature"] = sig

    try:
        sb = sb_for_user()
        dept_ids, grp_ids = get_user_scope(sb, tenant_id, user_id, role=role)
    except Exception:
        dept_ids, grp_ids = None, None
    st.session_state["scope_departamento_ids"] = dept_ids
    st.session_state["scope_grupo_ids"]        = grp_ids


# ── Roteamento ────────────────────────────────────────────────────────────────
def _build_route(role: str) -> dict:
    """Monta o dict de roteamento com guards de role embutidos."""
    def _guarded(render_fn, *roles):
        require_role(*roles)
        render_fn()

    return {
        PageKey.INICIO.value:               render_home_overview,
        PageKey.DASHBOARD.value:            render_dashboard,
        PageKey.PAINEL_GESTOR.value:        render_gestor_painel,
        PageKey.NOTIFICACOES.value:         render_notificacoes,
        PageKey.AUDITORIA.value:            lambda: _guarded(render_auditoria,              *Role.MANAGER_ROLES),
        PageKey.APONTAMENTO.value:          lambda: _guarded(render_apontamento,             *Role.ADMIN_ROLES),
        PageKey.CONFIG_GUIADA.value:        lambda: _guarded(render_setup_wizard,            *Role.ADMIN_ROLES),
        PageKey.ADM_USUARIOS.value:         lambda: _guarded(render_admin_usuarios,          *Role.ADMIN_ROLES),
        PageKey.ADM_DEPARTAMENTOS.value:    lambda: _guarded(render_admin_departamentos,     *Role.ADMIN_ROLES),
        PageKey.ADM_GRUPOS.value:           lambda: _guarded(render_admin_grupos,            *Role.ADMIN_ROLES),
        PageKey.ADM_EQUIPAMENTOS.value:     lambda: _guarded(render_admin_equipamentos,      *Role.ADMIN_ROLES),
        PageKey.ADM_INTEGRIDADE.value:      lambda: _guarded(render_admin_integridade,       *Role.ADMIN_ROLES),
        PageKey.ADM_SETORES_SERVICOS.value: lambda: _guarded(render_admin_setores_servicos,  *Role.ADMIN_ROLES),
        PageKey.ADM_TEMPLATES.value:        lambda: _guarded(render_admin_templates,         *Role.ADMIN_ROLES),
        PageKey.ADM_REVISOES.value:         lambda: _guarded(render_admin_revisoes,          *Role.ADMIN_ROLES),
        PageKey.ADM_BRANDING.value:         lambda: _guarded(render_admin_branding_reports,  *Role.ADMIN_ROLES),
    }


# ── Entrypoint ────────────────────────────────────────────────────────────────
def main():
    st.session_state.setdefault("sb_access_token", None)

    render_mobile_toggle()
    if is_mobile():
        inject_mobile_css()

    if not st.session_state.get("sb_access_token"):
        render_login()
        return

    if not ensure_valid_token():
        hard_logout()
        return

    current_uid = st.session_state.get("sb_user_id") or ""
    _handle_identity_guard(current_uid)

    require_login()
    ensure_tenant_selected()
    refresh_current_role()

    role      = st.session_state.get("current_role", "") or ""
    user_id   = st.session_state.get("sb_user_id",   "") or ""
    tenant_id = st.session_state.get("current_tenant_id", "") or ""

    require_tenant_selected()
    _resolve_scope(tenant_id, user_id, role)

    # Páginas acessíveis para o role atual (via page_registry)
    # get_menu_pages exclui pages com group='detail' do menu (#5)
    pages = get_pages_for_role(role)        # todas (incluindo detail) — para roteamento
    menu_pages = get_menu_pages(role)       # apenas as visíveis no menu
    st.session_state["pages"] = pages
    st.session_state["menu_pages"] = menu_pages

    nav_to = st.session_state.pop("__nav_to", None)
    if nav_to in pages:
        st.session_state["__current_page"] = nav_to
    # Default para primeira página do menu (não uma 'detail') (#5)
    if "__current_page" not in st.session_state or st.session_state["__current_page"] not in pages:
        st.session_state["__current_page"] = menu_pages[0] if menu_pages else pages[0]

    current = st.session_state["__current_page"]

    # ── Navegação (mobile vs desktop) ─────────────────────────────────────────
    if is_mobile():
        core_pages  = [p for p in pages if NAV_CONFIG.get(p, ("", "core", ""))[1] == "core"]
        admin_pages = [p for p in pages if NAV_CONFIG.get(p, ("", "core", ""))[1] == "admin"]
        nav_opts    = core_pages + (["Admin"] if admin_pages else [])

        def _fmt(p: str) -> str:
            if p == "Admin": return "⚙ Admin"
            icon, _, label = NAV_CONFIG.get(p, ("·", "core", p))
            return f"{icon} {label}"

        with st.container():
            pick = st.selectbox("Navegação", nav_opts,
                                index=nav_opts.index(current) if current in nav_opts else 0,
                                format_func=_fmt, label_visibility="collapsed")
        selected = pick
        if pick == "Admin" and admin_pages:
            selected = st.selectbox("Administração", admin_pages, index=0)
        if selected != current:
            st.session_state["__current_page"] = selected
            st.rerun()
    else:
        selected = _render_sidebar(menu_pages, current, role, user_id, tenant_id, is_mobile())
        if selected != current:
            st.session_state["__current_page"] = selected
            st.rerun()

    page = st.session_state["__current_page"]
    st.session_state["__menu"] = page

    # ── Roteamento ────────────────────────────────────────────────────────────
    if page == PageKey.MATRIZ.value:
        st.session_state["matriz_read_only"] = not Role.is_admin(role)
        render_matriz()
    else:
        ROUTE = _build_route(role)
        if page in ROUTE:
            ROUTE[page]()
        else:
            st.info("Página não encontrada.")


if __name__ == "__main__":
    main()
