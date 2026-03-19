from src.ui.core.styles import page_header as _ph
import streamlit as st
from postgrest.exceptions import APIError

from src.utils.supabase_helpers import sb_for_user, current_tenant_id, current_role
from src.utils import nav
from src.services.demo_seed import seed_demo_data


def _count(sb, table, tenant_id, extra_filters=None):
    """Count rows in a tenant-scoped table (works even for join tables without 'id')."""
    try:
        q = sb.table(table).select(
            "tenant_id",
            count="exact").eq(
            "tenant_id",
            tenant_id)
        if extra_filters:
            q = extra_filters(q)
        res = q.execute()
        return res.count or 0
    except APIError:
        return 0
    except Exception:
        return 0


def render_setup_wizard() -> None:
    _ph("⚙", "Configuração Guiada",
        "Checklist completo para deixar o sistema pronto rapidamente. Apenas Admin.")

    role = current_role()
    if role not in ("admin", "superadmin"):
        st.error("Apenas Admin pode acessar a configuração guiada.")
        st.stop()

    tenant_id = current_tenant_id()
    sb = sb_for_user()

    c_setores = _count(sb, "setores", tenant_id, lambda q: q.eq("ativo", True))
    c_serv = _count(sb, "servicos", tenant_id, lambda q: q.eq("ativo", True))
    c_grupos = _count(
        sb,
        "equip_grupos",
        tenant_id,
        lambda q: q.eq(
            "ativo",
            True))
    c_eq = _count(sb, "equipamentos", tenant_id, lambda q: q.eq("ativo", True))
    c_templates = _count(sb, "grupo_servicos", tenant_id)
    c_rev = _count(sb, "revisoes", tenant_id)
    c_tasks = _count(sb, "tarefas_servico", tenant_id)

    steps = [
        ("Setores", c_setores, "Admin - Setores & Serviços"),
        ("Serviços", c_serv, "Admin - Setores & Serviços"),
        ("Grupos", c_grupos, "Admin - Grupos"),
        ("Equipamentos", c_eq, "Admin - Equipamentos"),
        ("Templates (Grupo → Serviços)", c_templates, "Admin - Templates"),
        ("Revisões", c_rev, "Admin - Revisões"),
        ("Matriz (tarefas geradas)", c_tasks, "Admin - Revisões"),
    ]

    st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)

    # Cards de status do setup
    cols = st.columns(4)
    for i, (name, count, _page) in enumerate(steps):
        is_ok = count > 0
        color = "#38A169" if is_ok else "#D69E2E"
        bg = "rgba(56,161,105,0.08)" if is_ok else "rgba(214,158,46,0.08)"
        border = "rgba(56,161,105,0.25)" if is_ok else "rgba(214,158,46,0.25)"
        icon_sym = "✓" if is_ok else "!"
        with cols[i % 4]:
            st.markdown(
                f'''<div style="background:{bg};border:1px solid {border};border-radius:10px;
                             padding:12px 14px;margin-bottom:4px">
                  <div style="display:flex;align-items:center;gap:8px">
                    <div style="width:22px;height:22px;background:{color};border-radius:50%;
                                display:flex;align-items:center;justify-content:center;
                                font-size:0.70rem;font-weight:800;color:#fff;flex-shrink:0">{icon_sym}</div>
                    <div>
                      <div style="font-size:0.78rem;font-weight:600;color:#E8EDF5">{name}</div>
                      <div style="font-size:0.68rem;color:#8892A4">{count} registro(s)</div>
                    </div>
                  </div>
                </div>''', unsafe_allow_html=True)

    st.divider()

    st.markdown("### Atalhos")
    st.caption(
        "Clique para abrir a tela correspondente e completar o setup. (Depois volte aqui.)")

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("1) Setores/Serviços", use_container_width=True):
            nav.goto("Admin - Setores & Serviços")
    with col2:
        if st.button("2) Grupos", use_container_width=True):
            nav.goto("Admin - Grupos")
    with col3:
        if st.button("3) Equipamentos", use_container_width=True):
            nav.goto("Admin - Equipamentos")

    col4, col5, col6 = st.columns(3)
    with col4:
        if st.button("4) Templates", use_container_width=True):
            nav.goto("Admin - Templates")
    with col5:
        if st.button(
            "5) Revisões + Matriz",
            type="primary",
                use_container_width=True):
            nav.goto("Admin - Revisões")
    with col6:
        if st.button("6) Permissões por setor", use_container_width=True):
            nav.goto("Admin - Usuários")

    st.divider()

    st.markdown("### Dados de demonstração (teste rápido)")
    st.caption("Cria setores, serviços, grupos, alguns equipamentos, templates e uma revisão demo. Pode rodar mais de uma vez.")
    colx, coly = st.columns([0.7, 0.3])
    with colx:
        confirm = st.checkbox(
            "Confirmo que quero inserir dados de demonstração neste tenant",
            value=False)
    with coly:
        if st.button(
            "Gerar dados demo",
            icon=":material/science:",
            type="primary",
            use_container_width=True,
                disabled=not confirm):
            try:
                with st.spinner("Gerando dados de demonstração..."):
                    result = seed_demo_data(str(tenant_id))
                st.success(
                    f"Demo criado: {
                        result['setores']} setores, {
                        result['servicos']} serviços, " f"{
                        result['grupos']} grupos, {
                        result['equipamentos']} equipamentos. Revisão ativa pronta.")
                nav.goto("Admin - Revisões")
            except Exception as e:
                st.error(f"Erro ao gerar demo: {e}")

    st.divider()

    if c_setores and c_serv and c_grupos and c_eq and c_templates and c_rev:
        st.success(
            "Setup mínimo completo. Agora é só gerar a matriz na tela de Revisões e operar no Apontamento!")
    else:
        st.info(
            "Complete os itens em amarelo. Assim que tudo estiver OK, você consegue operar sem Excel.")
