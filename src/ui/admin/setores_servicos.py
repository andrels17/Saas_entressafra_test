"""Admin — Setores & Serviços.

Melhorias UI/UX v2:
- Cards visuais com badge ativo/inativo
- Criação rápida inline no topo
- Edição expandível por item (não polui a lista)
- Busca por texto em tempo real
- Contadores com pills
- Serviços agrupados por setor
"""
from __future__ import annotations
from html import escape as _h

from collections import defaultdict

import streamlit as st
from src.utils import nav
from src.utils.supabase_helpers import sanitize_user_input,\
     sb_for_user, current_tenant_id, current_role
from src.ui.core.styles import page_header as _ph
from src.ui.admin_components.utils import inject_enterprise_css


# ── CSS local ─────────────────────────────────────────────────────────────────

def _inject_css() -> None:
    inject_enterprise_css()
    st.markdown("""
<style>
.ss-item-card {
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 12px;
    padding: 10px 14px;
    margin: 4px 0 2px 0;
    transition: border-color .15s;
}
.ss-item-card:hover { border-color: rgba(255,255,255,0.20); }
.ss-item-name { font-weight: 600; font-size: 0.96rem; color: #F1F5F9; }
.ss-badge-active {
    display:inline-block; padding:2px 9px; border-radius:999px; font-size:11px;
    font-weight:700; background:rgba(34,197,94,.15); color:#4ADE80;
    border:1px solid rgba(34,197,94,.30); margin-left:8px; vertical-align:middle;
}
.ss-badge-inactive {
    display:inline-block; padding:2px 9px; border-radius:999px; font-size:11px;
    font-weight:700; background:rgba(148,163,184,.12); color:#94A3B8;
    border:1px solid rgba(148,163,184,.22); margin-left:8px; vertical-align:middle;
}
.ss-section-header {
    font-size:0.78rem; font-weight:700; color:#64748B; text-transform:uppercase;
    letter-spacing:.09em; margin: 16px 0 6px 0;
}
.ss-create-area {
    background: rgba(255,255,255,0.02);
    border: 1px dashed rgba(255,255,255,0.11);
    border-radius: 14px;
    padding: 12px 14px 10px 14px;
    margin-bottom: 14px;
}
.ss-stat-line {
    color:#94A3B8; font-size:0.83rem; margin-bottom:10px;
}
.ss-stat-pill {
    display:inline-block; padding:2px 9px; border-radius:999px; font-size:11px;
    font-weight:600; background:rgba(255,255,255,0.06); color:#CBD5E1;
    border:1px solid rgba(255,255,255,0.09); margin-left:5px;
}
</style>
""", unsafe_allow_html=True)


def _badge(ativo: bool) -> str:
    if ativo:
        return '<span class="ss-badge-active">Ativo</span>'
    return '<span class="ss-badge-inactive">Inativo</span>'


def _pill(text: str) -> str:
    return f'<span class="ss-stat-pill">{text}</span>'


# ── Tab Setores ───────────────────────────────────────────────────────────────

def _tab_setores(sb, tenant_id: str) -> None:
    # Criar novo
    st.markdown('<div class="ss-section-header">Novo setor</div>', unsafe_allow_html=True)
    st.markdown('<div class="ss-create-area">', unsafe_allow_html=True)
    col_i, col_b = st.columns([4, 1])
    with col_i:
        novo_nome = st.text_input(
            "nome", placeholder="Ex.: Mecânica, Elétrica, Hidráulica…",
            label_visibility="collapsed", key="setor_novo_nome",
        )
    with col_b:
        criar = st.button("＋ Criar", key="setor_criar_btn", type="primary", use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

    if criar:
        nome = (novo_nome or "").strip()
        if not nome:
            st.warning("⚠️ Informe um nome para o setor.")
        else:
            try:
                sb.table("setores").insert({"tenant_id": tenant_id, "nome": sanitize_user_input(nome, max_length=100)}).execute()
                st.toast("✅ Setor criado!", icon=":material/check_circle:")
                nav.rerun_keep_menu()
            except Exception as e:
                st.error(f"Erro ao criar setor: {e}")

    # Filtros
    st.markdown('<div class="ss-section-header">Setores cadastrados</div>', unsafe_allow_html=True)
    col_busca, col_tog = st.columns([3, 1])
    with col_busca:
        busca = st.text_input(
            "busca", placeholder="🔍  Filtrar por nome…",
            label_visibility="collapsed", key="setor_busca",
        )
    with col_tog:
        only_active = st.toggle("Apenas ativos", value=True, key="setores_only_active")

    q = sb.table("setores").select("id,nome,ativo").eq("tenant_id", tenant_id).order("nome")
    if only_active:
        q = q.eq("ativo", True)
    setores = q.execute().data or []

    if busca:
        bl = busca.strip().lower()
        setores = [s for s in setores if bl in s["nome"].lower()]

    if not setores:
        st.info("Nenhum setor encontrado.")
        return

    ativos = sum(1 for s in setores if s["ativo"])
    st.markdown(
        f'<div class="ss-stat-line">Exibindo <strong style="color:#CBD5E1">{len(setores)}</strong> setor(es)'
        f'{_pill(f"{ativos} ativos")}{_pill(f"{len(setores)-ativos} inativos")}</div>',
        unsafe_allow_html=True,
    )

    for s in setores:
        sid = s["id"]
        edit_key = f"setor_edit_open_{sid}"
        if edit_key not in st.session_state:
            st.session_state[edit_key] = False

        st.markdown(
            f'<div class="ss-item-card"><span class="ss-item-name">{_h(str(s["nome"] or ""))}</span>{_badge(s["ativo"])}</div>',
            unsafe_allow_html=True,
        )
        col_e, col_t, col_sp = st.columns([1, 1, 4])
        with col_e:
            lbl_e = "✕ Fechar" if st.session_state[edit_key] else "✏️ Renomear"
            if st.button(lbl_e, key=f"setor_edit_toggle_{sid}", use_container_width=True):
                st.session_state[edit_key] = not st.session_state[edit_key]
                st.rerun()
        with col_t:
            lbl_t = "🔴 Desativar" if s["ativo"] else "🟢 Ativar"
            if st.button(lbl_t, key=f"setor_toggle_{sid}", use_container_width=True):
                try:
                    sb.table("setores").update({"ativo": not s["ativo"]}).eq("id", sid).execute()
                    st.toast("✅ Status atualizado.", icon=":material/check_circle:")
                    nav.rerun_keep_menu()
                except Exception as e:
                    st.error(f"Erro: {e}")

        if st.session_state.get(edit_key):
            novo = st.text_input("Novo nome", value=s["nome"], key=f"setor_rename_{sid}")
            if st.button("💾 Salvar", key=f"setor_save_{sid}", type="primary"):
                nn = (novo or "").strip()
                if not nn:
                    st.warning("Nome inválido.")
                else:
                    try:
                        sb.table("setores").update({"nome": nn}).eq("id", sid).execute()
                        st.toast("✅ Nome atualizado!", icon=":material/check_circle:")
                        st.session_state[edit_key] = False
                        nav.rerun_keep_menu()
                    except Exception as e:
                        st.error(f"Erro ao salvar: {e}")

        st.markdown("<div style='height:3px'></div>", unsafe_allow_html=True)


# ── Tab Serviços ──────────────────────────────────────────────────────────────

def _tab_servicos(sb, tenant_id: str) -> None:
    setores_all = (
        sb.table("setores").select("id,nome,ativo")
        .eq("tenant_id", tenant_id).order("nome").execute().data
    ) or []
    setores_ativos = [s for s in setores_all if s["ativo"]]
    if not setores_ativos:
        st.warning("⚠️ Cadastre e ative pelo menos um setor antes de criar serviços.")
        return

    setor_map = {s["nome"]: s["id"] for s in setores_ativos}

    # Criar novo
    st.markdown('<div class="ss-section-header">Novo serviço</div>', unsafe_allow_html=True)
    st.markdown('<div class="ss-create-area">', unsafe_allow_html=True)
    col_s, col_n, col_b = st.columns([1.8, 2.5, 1])
    with col_s:
        setor_escolhido = st.selectbox(
            "Setor", list(setor_map.keys()),
            key="serv_novo_setor", label_visibility="collapsed",
        )
    with col_n:
        novo_serv = st.text_input(
            "nome", placeholder="Ex.: Motor, Freios, Vazamento…",
            label_visibility="collapsed", key="serv_novo_nome",
        )
    with col_b:
        criar_serv = st.button("＋ Criar", key="serv_criar_btn", type="primary", use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

    if criar_serv:
        nome = (novo_serv or "").strip()
        if not nome:
            st.warning("⚠️ Informe um nome para o serviço.")
        else:
            try:
                sb.table("servicos").insert({
                    "tenant_id": tenant_id,
                    "setor_id": setor_map[setor_escolhido],
                    "nome": sanitize_user_input(nome, max_length=100),
                }).execute()
                st.toast("✅ Serviço criado!", icon=":material/check_circle:")
                nav.rerun_keep_menu()
            except Exception as e:
                st.error(f"Erro ao criar serviço: {e}")

    # Filtros
    st.markdown('<div class="ss-section-header">Serviços cadastrados</div>', unsafe_allow_html=True)
    col_b2, col_f, col_t = st.columns([2, 1.5, 1])
    with col_b2:
        busca_s = st.text_input(
            "busca", placeholder="🔍  Filtrar por nome…",
            label_visibility="collapsed", key="serv_busca",
        )
    with col_f:
        filtro_setor = st.selectbox(
            "setor", ["(Todos)"] + sorted({s["nome"] for s in setores_all}),
            key="serv_filtro_setor", label_visibility="collapsed",
        )
    with col_t:
        only_active_s = st.toggle("Apenas ativos", value=True, key="servicos_only_active")

    q = (
        sb.table("servicos").select("id,nome,ativo,setor_id,setores(nome)")
        .eq("tenant_id", tenant_id).order("nome")
    )
    if only_active_s:
        q = q.eq("ativo", True)
    servicos = q.execute().data or []

    if filtro_setor != "(Todos)":
        servicos = [sv for sv in servicos if (sv.get("setores") or {}).get("nome") == filtro_setor]
    if busca_s:
        bl = busca_s.strip().lower()
        servicos = [sv for sv in servicos if bl in sv["nome"].lower()]

    if not servicos:
        st.info("Nenhum serviço encontrado para os filtros selecionados.")
        return

    ativos_s = sum(1 for sv in servicos if sv["ativo"])
    st.markdown(
        f'<div class="ss-stat-line">Exibindo <strong style="color:#CBD5E1">{len(servicos)}</strong> serviço(s)'
        f'{_pill(f"{ativos_s} ativos")}{_pill(f"{len(servicos)-ativos_s} inativos")}</div>',
        unsafe_allow_html=True,
    )

    # Agrupar por setor
    by_setor: dict[str, list] = defaultdict(list)
    for sv in servicos:
        sn = (sv.get("setores") or {}).get("nome") or "Sem setor"
        by_setor[sn].append(sv)

    auto_expand = len(by_setor) == 1
    for setor_label in sorted(by_setor.keys()):
        lista = by_setor[setor_label]
        with st.expander(f"**{setor_label}** — {len(lista)} serviço(s)", expanded=auto_expand):
            for sv in lista:
                svid = sv["id"]
                edit_key = f"serv_edit_open_{svid}"
                if edit_key not in st.session_state:
                    st.session_state[edit_key] = False

                st.markdown(
                    f'<div class="ss-item-card"><span class="ss-item-name">{_h(str(sv["nome"] or ""))}</span>{_badge(sv["ativo"])}</div>',
                    unsafe_allow_html=True,
                )
                col_e, col_t2, col_sp = st.columns([1, 1, 4])
                with col_e:
                    lbl_e = "✕ Fechar" if st.session_state[edit_key] else "✏️ Renomear"
                    if st.button(lbl_e, key=f"serv_edit_toggle_{svid}", use_container_width=True):
                        st.session_state[edit_key] = not st.session_state[edit_key]
                        st.rerun()
                with col_t2:
                    lbl_t = "🔴 Desativar" if sv["ativo"] else "🟢 Ativar"
                    if st.button(lbl_t, key=f"serv_toggle_{svid}", use_container_width=True):
                        try:
                            sb.table("servicos").update({"ativo": not sv["ativo"]}).eq("id", svid).execute()
                            st.toast("✅ Status atualizado.", icon=":material/check_circle:")
                            nav.rerun_keep_menu()
                        except Exception as e:
                            st.error(f"Erro: {e}")

                if st.session_state.get(edit_key):
                    novo_n = st.text_input("Novo nome", value=sv["nome"], key=f"serv_rename_{svid}")
                    if st.button("💾 Salvar", key=f"serv_save_{svid}", type="primary"):
                        nn = (novo_n or "").strip()
                        if not nn:
                            st.warning("Nome inválido.")
                        else:
                            try:
                                sb.table("servicos").update({"nome": nn}).eq("id", svid).execute()
                                st.toast("✅ Nome atualizado!", icon=":material/check_circle:")
                                st.session_state[edit_key] = False
                                nav.rerun_keep_menu()
                            except Exception as e:
                                st.error(f"Erro ao salvar: {e}")

                st.markdown("<div style='height:3px'></div>", unsafe_allow_html=True)


# ── Entry point ───────────────────────────────────────────────────────────────

def render_admin_setores_servicos() -> None:
    _ph("◧", "Setores & Serviços",
        "Cadastre setores e serviços — alimentam os Templates e a Matriz.")

    role = current_role()
    if role not in ("admin", "superadmin"):
        st.error("🔒 Apenas Admin pode gerenciar setores e serviços.")
        st.stop()

    _inject_css()

    tenant_id = current_tenant_id()
    sb = sb_for_user()

    tab1, tab2 = st.tabs(["🗂️  Setores", "🔧  Serviços"])
    with tab1:
        _tab_setores(sb, tenant_id)
    with tab2:
        _tab_servicos(sb, tenant_id)
