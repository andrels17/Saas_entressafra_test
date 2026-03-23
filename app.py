import base64
import html
import json
import sys
import time
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

# ── Core UI ───────────────────────────────────────────────────────────────────
from src.ui.core.login import render_login
from src.ui.core.page_registry import NAV_CONFIG, PageKey, get_menu_pages, get_pages_for_role
from src.ui.core.setup_wizard import render_setup_wizard
from src.ui.core.sidebar_counts import sidebar_badges
from src.ui.core.sidebar_display import get_display_names, role_label
from src.ui.core.styles import inject_global_css, inject_mobile_css

# ── Auth ──────────────────────────────────────────────────────────────────────
from src.auth.audit import audit_logout
from src.auth.guard import require_login, require_role, require_tenant_selected
from src.auth.permissions import can_view_all_data
from src.auth.roles import Role
from src.auth.scope import get_user_scope
from src.auth.session import clear_derived_state, ensure_valid_token, hard_logout
from src.auth.tenant import ensure_tenant_selected, refresh_current_role
from src.utils.config import validate_config_or_stop

# ── Utils ─────────────────────────────────────────────────────────────────────
from src.utils.mobile import detect_screen_width, is_mobile
from src.utils.supabase_helpers import sb_for_user

# ── Pages ─────────────────────────────────────────────────────────────────────
from src.ui.pages.apontamento import render_apontamento
from src.ui.pages.auditoria import render_auditoria
from src.ui.pages.dashboard import render_dashboard
from src.ui.pages.gestor_painel import render_gestor_painel
from src.ui.pages.home_overview import render_home_overview
from src.ui.pages.matriz import render_matriz
from src.ui.pages.notificacoes import render_notificacoes

# ── Admin ─────────────────────────────────────────────────────────────────────
from src.ui.admin.branding_reports import render_admin_branding_reports
from src.ui.admin.departamentos import render_admin_departamentos
from src.ui.admin.equipamentos import render_admin_equipamentos
from src.ui.admin.grupos import render_admin_grupos
from src.ui.admin.integridade import render_admin_integridade
from src.ui.admin.revisoes import render_admin_revisoes
from src.ui.admin.setores_servicos import render_admin_setores_servicos
from src.ui.admin.templates import render_admin_templates
from src.ui.admin.usuarios import render_admin_usuarios


st.set_page_config(
    page_title=st.secrets.get("APP_NAME", "AgroSafra"),
    page_icon="🌾",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_global_css()


def _escape(value: object) -> str:
    return html.escape(str(value or ""))


def _normalize_role(value: object) -> str:
    role = str(getattr(value, "value", value) or "").strip().lower()
    aliases = {
        "manager": "gestor",
        "executor": "user",
        "usuario": "user",
        "member": "user",
    }
    return aliases.get(role, role)


def _inject_sidebar_css() -> None:
    css_path = Path(__file__).parent / "src" / "ui" / "core" / "sidebar.css"
    st.markdown(f"<style>{css_path.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


def _safe_clear_streamlit_caches() -> None:
    for fn_name in ("cache_data", "cache_resource"):
        fn = getattr(st, fn_name, None)
        if fn and hasattr(fn, "clear"):
            try:
                fn.clear()
            except Exception:
                pass


def _purge_login_screen_state() -> None:
    """Evita herança de estado visual/derivado quando não há token ativo."""
    clear_derived_state()
    for key in (
        "_identity_user_id",
        "_role_identity_signature",
        "_sidebar_badges_cache",
        "_sidebar_rev_titulo",
        "_sidebar_rev_semana",
        "tenant_logo_url",
    ):
        st.session_state.pop(key, None)


def _handle_identity_guard(current_uid: str) -> None:
    """Limpa estado derivado quando o usuário muda na mesma sessão do servidor."""
    prev_uid = st.session_state.get("_identity_user_id") or ""
    if prev_uid and current_uid and prev_uid != current_uid:
        clear_derived_state()
        for key in (
            "_role_identity_signature",
            "_sidebar_badges_cache",
            "_sidebar_rev_titulo",
            "_sidebar_rev_semana",
            "tenant_logo_url",
        ):
            st.session_state.pop(key, None)
        _safe_clear_streamlit_caches()
    st.session_state["_identity_user_id"] = current_uid


def _token_expires_soon(token: str, buffer_seconds: int = 60) -> bool:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        payload = json.loads(base64.b64decode(part))
        return payload.get("exp", 0) - time.time() < buffer_seconds
    except Exception:
        return True


def _ensure_authenticated_session() -> bool:
    st.session_state.setdefault("sb_access_token", None)
    validate_config_or_stop()
    detect_screen_width()
    inject_mobile_css()

    token = st.session_state.get("sb_access_token")
    if not token:
        _purge_login_screen_state()
        render_login()
        return False

    if _token_expires_soon(str(token)):
        if not ensure_valid_token():
            hard_logout()
            return False

    require_login()
    return True


def _resolve_scope(tenant_id: str, user_id: str, role: str) -> None:
    signature = f"{user_id}:{tenant_id}:{role}"
    prev_signature = st.session_state.get("_scope_signature")

    if prev_signature == signature:
        return

    if prev_signature and prev_signature != signature:
        for key in ("__current_page", "__nav_to", "__menu", "menu"):
            st.session_state.pop(key, None)

    st.session_state["_scope_signature"] = signature

    try:
        sb = sb_for_user()
        dept_ids, grp_ids = get_user_scope(sb, tenant_id, user_id, role=role)
    except Exception:
        dept_ids, grp_ids = (None, None) if can_view_all_data(role) else ([], [])

    st.session_state["scope_departamento_ids"] = dept_ids
    st.session_state["scope_grupo_ids"] = grp_ids


def _load_navigation_context() -> tuple[str, str, str]:
    """Valida tenant/contexto e devolve role, user_id, tenant_id já revalidados."""
    ensure_tenant_selected()

    user_id = str(st.session_state.get("sb_user_id", "") or "")
    tenant_id = str(st.session_state.get("current_tenant_id", "") or "")

    require_tenant_selected()

    identity_signature = f"{user_id}:{tenant_id}"
    prev_identity_signature = st.session_state.get("_role_identity_signature")
    current_role = _normalize_role(st.session_state.get("current_role", ""))

    must_refresh_role = (
        not current_role
        or prev_identity_signature != identity_signature
    )

    if must_refresh_role:
        refreshed_role = _normalize_role(refresh_current_role())
        if refreshed_role:
            current_role = refreshed_role
            st.session_state["current_role"] = current_role
        st.session_state["_role_identity_signature"] = identity_signature
        st.session_state.pop("_sidebar_badges_cache", None)
    else:
        st.session_state["current_role"] = current_role

    _resolve_scope(tenant_id, user_id, current_role)
    return current_role, user_id, tenant_id


def _store_available_pages(role: str) -> tuple[list[str], list[str]]:
    pages = get_pages_for_role(role)
    menu_pages = get_menu_pages(role)
    st.session_state["pages"] = pages
    st.session_state["menu_pages"] = menu_pages
    return pages, menu_pages


def _sync_current_page(pages: list[str], menu_pages: list[str]) -> str:
    nav_to = st.session_state.pop("__nav_to", None)
    if nav_to in pages:
        st.session_state["__current_page"] = nav_to

    if "__current_page" not in st.session_state or st.session_state["__current_page"] not in pages:
        st.session_state["__current_page"] = menu_pages[0] if menu_pages else pages[0]

    return st.session_state["__current_page"]


def _safe_sidebar_badges(user_id: str, tenant_id: str, role: str) -> dict[str, int]:
    sig = f"{user_id}:{tenant_id}:{role}"
    now = time.time()
    cached = st.session_state.get("_sidebar_badges_cache") or {}
    if cached.get("sig") == sig and (now - float(cached.get("at", 0))) < 20:
        return cached.get("data") or {"gestor_travados": 0, "apont_pendentes": 0, "auditoria_24h": 0}

    try:
        data = sidebar_badges()
    except Exception:
        data = {"gestor_travados": 0, "apont_pendentes": 0, "auditoria_24h": 0}

    st.session_state["_sidebar_badges_cache"] = {"sig": sig, "at": now, "data": data}
    return data


def _render_sidebar_header(app_name: str, tenant_id: str, user_id: str, role: str) -> None:
    tenant_name, user_name = get_display_names(tenant_id, user_id)
    role_text = role_label(role)
    role_norm = _normalize_role(role)
    role_css = {
        "admin": "role-admin",
        "superadmin": "role-superadmin",
        "gestor": "role-gestor",
        "supervisor": "role-gestor",
    }.get(role_norm, "role-user")
    avatar = (user_name[:1] or "U").upper()
    logo_url = st.session_state.get("tenant_logo_url")

    app_name_safe = _escape(app_name)
    tenant_name_safe = _escape(tenant_name)
    user_name_safe = _escape(user_name)
    role_text_safe = _escape(role_text)

    if logo_url:
        st.image(logo_url, width=140, use_container_width=False)
        if st.button(f"🌾 {app_name}", key="sb_logo_home_btn", use_container_width=True, type="tertiary"):
            st.session_state["__nav_to"] = PageKey.INICIO.value
            st.rerun()
    else:
        st.markdown(
            f"""
            <div class="sb-top">
              <div class="sb-approw">
                <div class="sb-app">
                  <span class="sb-app-ico">🌾</span>
                  <span class="sb-app-name" title="{app_name_safe}">{app_name_safe}</span>
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
          <div class="sb-avatar">{_escape(avatar)}</div>
          <div class="sb-usertext">
            <div class="sb-username" title="{user_name_safe}">{user_name_safe}</div>
            <div class="sb-meta-row">
              <div class="sb-tenant" title="{tenant_name_safe}">{tenant_name_safe}</div>
              <span class="role-chip {role_css}">{role_text_safe}</span>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    review_title = st.session_state.get("_sidebar_rev_titulo")
    review_week = st.session_state.get("_sidebar_rev_semana")
    if review_title:
        week_text = f" · Sem. {_escape(review_week)}" if review_week else ""
        st.markdown(
            f'<div class="sb-revisao-chip">📋 {_escape(review_title)}{week_text}</div>',
            unsafe_allow_html=True,
        )


def _render_sidebar_logout(mobile: bool) -> None:
    if mobile:
        return

    st.markdown(
        '<div class="sb-footer"><div class="sb-footer-title">Sessão</div>'
        '<div class="sb-footer-text">Navegue pelo menu lateral e encerre sua sessão ao sair.</div></div>',
        unsafe_allow_html=True,
    )
    if st.button("Sair", icon=":material/logout:", key="sidebar_logout", use_container_width=True, type="tertiary"):
        audit_logout(st.session_state.get("sb_user_id"))
        hard_logout()


def _nav_button(label: str, *, key: str, selected: bool) -> bool:
    return st.button(
        label,
        key=key,
        use_container_width=True,
        type="primary" if selected else "secondary",
    )


def _render_sidebar_navigation(pages: list[str], current_page: str, badges: dict[str, int]) -> str:
    selected = current_page
    core_pages = [p for p in pages if NAV_CONFIG.get(p, ("", "core", ""))[1] == "core"]
    admin_pages = [p for p in pages if NAV_CONFIG.get(p, ("", "core", ""))[1] == "admin"]

    page_badges: dict[str, int] = {
        PageKey.PAINEL_GESTOR.value: badges.get("gestor_travados", 0),
        PageKey.APONTAMENTO.value: badges.get("apont_pendentes", 0),
        PageKey.AUDITORIA.value: badges.get("auditoria_24h", 0),
    }

    def fmt(page: str) -> str:
        icon, _, label = NAV_CONFIG.get(page, ("•", "core", page))
        count = page_badges.get(page, 0)
        badge = f"  ({count})" if count > 0 else ""
        return f"{icon}  {label}{badge}"

    if core_pages:
        st.markdown('<div class="nav-section-label">Principal</div>', unsafe_allow_html=True)
        for page in core_pages:
            if _nav_button(fmt(page), key=f"nav_core_btn_{page}", selected=(selected == page)):
                selected = page

    if admin_pages:
        admin_open_key = "_sidebar_admin_open"
        st.session_state.setdefault(admin_open_key, current_page in admin_pages)
        if current_page in admin_pages:
            st.session_state[admin_open_key] = True

        admin_open = bool(st.session_state.get(admin_open_key, False))
        caret = "▾" if admin_open else "▸"
        shell_class = "admin-shell open" if admin_open else "admin-shell"

        st.markdown('<div class="nav-section-label">Administração</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="{shell_class}">', unsafe_allow_html=True)
        if st.button(f"{caret}  Administração", key="admin_toggle_btn", use_container_width=True, type="secondary"):
            admin_open = not admin_open
            st.session_state[admin_open_key] = admin_open

        if admin_open:
            st.markdown(
                '<div class="admin-note">Configurações, cadastros e manutenção do tenant.</div>',
                unsafe_allow_html=True,
            )
            for page in admin_pages:
                if _nav_button(fmt(page), key=f"nav_admin_btn_{page}", selected=(selected == page)):
                    selected = page

        st.markdown("</div>", unsafe_allow_html=True)

    return selected


def _render_sidebar(pages: list[str], current_page: str, role: str, user_id: str, tenant_id: str, mobile: bool) -> str:
    selected = current_page
    with st.sidebar:
        _inject_sidebar_css()
        _render_sidebar_header(st.secrets.get("APP_NAME", "AgroSafra"), tenant_id, user_id, role)
        badges = _safe_sidebar_badges(user_id, tenant_id, role)
        _render_sidebar_logout(mobile)
        selected = _render_sidebar_navigation(pages, current_page, badges)
    return selected


def _render_mobile_nav(menu_pages: list[str], current: str) -> None:
    core_pages = [p for p in menu_pages if NAV_CONFIG.get(p, ("", "core", ""))[1] == "core"]
    admin_pages = [p for p in menu_pages if NAV_CONFIG.get(p, ("", "core", ""))[1] == "admin"]

    def fmt(page: str) -> str:
        icon, _, label = NAV_CONFIG.get(page, ("·", "core", page))
        return f"{icon}  {label}"

    c_nav, c_out = st.columns([5, 1])
    with c_nav:
        all_opts = core_pages + (admin_pages if admin_pages else [])
        idx = all_opts.index(current) if current in all_opts else 0
        picked = st.selectbox(
            "Página",
            all_opts,
            index=idx,
            format_func=fmt,
            key="mob_nav_sel",
            label_visibility="collapsed",
        )

    with c_out:
        if st.button("⎋", key="mob_logout_btn", help="Sair", use_container_width=True):
            hard_logout()

    if picked != current:
        st.session_state["__current_page"] = picked
        st.rerun()

    max_pills = 5
    pill_pages = core_pages[:max_pills]
    if len(pill_pages) > 1:
        pill_labels = [
            NAV_CONFIG.get(page, ("·", "core", page))[0] + "  " + NAV_CONFIG.get(page, ("·", "core", page))[2]
            for page in pill_pages
        ]
        current_label = (
            NAV_CONFIG.get(current, ("·", "core", current))[0] + "  " + NAV_CONFIG.get(current, ("·", "core", current))[2]
            if current in pill_pages
            else None
        )
        selected_pill = st.pills(
            "nav",
            pill_labels,
            default=current_label,
            key="mob_pills",
            label_visibility="collapsed",
        )
        if selected_pill:
            idx_pill = pill_labels.index(selected_pill)
            picked_page = pill_pages[idx_pill]
            if picked_page != current:
                st.session_state["__current_page"] = picked_page
                st.rerun()

    st.divider()


def _build_route() -> dict[str, callable]:
    def guarded(render_fn, *roles):
        require_role(*roles)
        render_fn()

    return {
        PageKey.INICIO.value: render_home_overview,
        PageKey.DASHBOARD.value: render_dashboard,
        PageKey.PAINEL_GESTOR.value: render_gestor_painel,
        PageKey.NOTIFICACOES.value: render_notificacoes,
        PageKey.AUDITORIA.value: lambda: guarded(render_auditoria, *Role.MANAGER_ROLES),
        PageKey.APONTAMENTO.value: lambda: guarded(render_apontamento, *Role.ADMIN_ROLES),
        PageKey.CONFIG_GUIADA.value: lambda: guarded(render_setup_wizard, *Role.ADMIN_ROLES),
        PageKey.ADM_USUARIOS.value: lambda: guarded(render_admin_usuarios, *Role.ADMIN_ROLES),
        PageKey.ADM_DEPARTAMENTOS.value: lambda: guarded(render_admin_departamentos, *Role.ADMIN_ROLES),
        PageKey.ADM_GRUPOS.value: lambda: guarded(render_admin_grupos, *Role.ADMIN_ROLES),
        PageKey.ADM_EQUIPAMENTOS.value: lambda: guarded(render_admin_equipamentos, *Role.ADMIN_ROLES),
        PageKey.ADM_INTEGRIDADE.value: lambda: guarded(render_admin_integridade, *Role.ADMIN_ROLES),
        PageKey.ADM_SETORES_SERVICOS.value: lambda: guarded(render_admin_setores_servicos, *Role.ADMIN_ROLES),
        PageKey.ADM_TEMPLATES.value: lambda: guarded(render_admin_templates, *Role.ADMIN_ROLES),
        PageKey.ADM_REVISOES.value: lambda: guarded(render_admin_revisoes, *Role.ADMIN_ROLES),
        PageKey.ADM_BRANDING.value: lambda: guarded(render_admin_branding_reports, *Role.ADMIN_ROLES),
    }


def _render_navigation(menu_pages: list[str], current_page: str, role: str, user_id: str, tenant_id: str) -> str:
    if is_mobile():
        st.markdown(
            "<style>section[data-testid='stSidebar']{display:none!important}</style>",
            unsafe_allow_html=True,
        )
        _render_mobile_nav(menu_pages, current_page)
        return st.session_state["__current_page"]

    selected = _render_sidebar(menu_pages, current_page, role, user_id, tenant_id, mobile=False)
    if selected != current_page:
        st.session_state["__current_page"] = selected
        st.rerun()
    return st.session_state["__current_page"]


def _render_current_page(page: str, role: str) -> None:
    if page == PageKey.MATRIZ.value:
        st.session_state["matriz_read_only"] = not Role.is_admin(role)
        render_matriz()
        return

    route = _build_route()
    handler = route.get(page)
    if handler:
        handler()
    else:
        st.info("Página não encontrada.")


def main() -> None:
    if not _ensure_authenticated_session():
        return

    current_uid = str(st.session_state.get("sb_user_id") or "")
    _handle_identity_guard(current_uid)

    role, user_id, tenant_id = _load_navigation_context()
    pages, menu_pages = _store_available_pages(role)
    current_page = _sync_current_page(pages, menu_pages)
    current_page = _render_navigation(menu_pages, current_page, role, user_id, tenant_id)

    st.session_state["__menu"] = current_page
    _render_current_page(current_page, role)


if __name__ == "__main__":
    main()
