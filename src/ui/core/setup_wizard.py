"""Setup Wizard com progresso visual real — checklist por etapa com status detalhado."""
from __future__ import annotations
from html import escape as _h

import streamlit as st
from postgrest.exceptions import APIError

from src.ui.core.styles import page_header as _ph
from src.utils.supabase_helpers import sb_for_user, current_tenant_id, current_role
from src.utils import nav
from src.services.demo_seed import seed_demo_data


def _count(sb, table, tenant_id, extra_filters=None) -> int:
    try:
        q = sb.table(table).select("tenant_id", count="exact").eq("tenant_id", tenant_id)
        if extra_filters:
            q = extra_filters(q)
        return q.execute().count or 0
    except (APIError, Exception):
        return 0


def _step_card(step_num: int, total: int, title: str, desc: str,
               count: int, required: bool, page: str) -> bool:
    """Renderiza um card de etapa com estado visual claro. Retorna True se completo."""
    is_ok = count > 0
    color   = "#38A169" if is_ok else ("#C53030" if required else "#D69E2E")
    bg      = f"{color}0F"
    border  = f"{color}33"
    icon    = "✓" if is_ok else ("!" if required else "·")
    opacity = "1" if is_ok else "0.95"

    st.markdown(
        f'''<div style="background:{bg};border:1px solid {border};border-radius:12px;
                        padding:14px 16px;margin-bottom:10px;opacity:{opacity}">
              <div style="display:flex;align-items:flex-start;gap:12px">
                <div style="flex-shrink:0;width:28px;height:28px;background:{color};
                            border-radius:50%;display:flex;align-items:center;
                            justify-content:center;font-size:0.75rem;font-weight:800;
                            color:#fff;margin-top:2px">{icon}</div>
                <div style="flex:1;min-width:0">
                  <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                    <span style="font-size:0.68rem;color:#8A9BAE;font-weight:600">
                      ETAPA {step_num}/{total}</span>
                    <span style="font-size:0.9rem;font-weight:700;color:#E8EDF5">
                      {_h(title)}</span>
                    {"" if required else
                     '<span style="font-size:0.64rem;background:rgba(255,255,255,0.07);'
                     'padding:2px 7px;border-radius:999px;color:#8A9BAE">opcional</span>'}
                  </div>
                  <div style="font-size:0.78rem;color:#8A9BAE;margin-top:3px">
                    {_h(desc)}</div>
                  <div style="font-size:0.75rem;margin-top:5px;font-weight:600;color:{color}">
                    {"✓ " + str(count) + " registro(s) cadastrado(s)" if is_ok
                     else "⚠ Nenhum registro encontrado"}</div>
                </div>
              </div>
            </div>''',
        unsafe_allow_html=True,
    )
    if not is_ok:
        if st.button(f"→ Ir para {_h(title)}", key=f"wiz_goto_{step_num}",
                     type="secondary", use_container_width=True):
            nav.goto(page)
    return is_ok


def render_setup_wizard() -> None:
    _ph("⚙", "Configuração Guiada",
        "Siga as etapas abaixo para deixar o sistema pronto para operar.")

    role = current_role()
    if role not in ("admin", "superadmin"):
        st.error("Apenas Admin pode acessar a configuração guiada.")
        st.stop()

    tenant_id = current_tenant_id()
    sb = sb_for_user()

    # ── Contagens ────────────────────────────────────────────────────────────
    with st.spinner("Verificando configuração…", show_time=False):
        c_setores    = _count(sb, "setores",         tenant_id, lambda q: q.eq("ativo", True))
        c_serv       = _count(sb, "servicos",        tenant_id, lambda q: q.eq("ativo", True))
        c_grupos     = _count(sb, "equip_grupos",    tenant_id, lambda q: q.eq("ativo", True))
        c_eq         = _count(sb, "equipamentos",    tenant_id, lambda q: q.eq("ativo", True))
        c_templates  = _count(sb, "grupo_servicos",  tenant_id)
        c_rev        = _count(sb, "revisoes",        tenant_id)
        c_tasks      = _count(sb, "tarefas_servico", tenant_id)

    steps_data = [
        ("Setores e Serviços", "Defina os setores (ex: Mecânica) e serviços (ex: Troca de óleo).",
         min(c_setores, c_serv), True, "Admin - Setores & Serviços"),
        ("Grupos de Equipamentos", "Crie grupos para organizar sua frota (ex: Escavadeiras).",
         c_grupos, True, "Admin - Grupos"),
        ("Equipamentos", "Importe ou cadastre os equipamentos de cada grupo.",
         c_eq, True, "Admin - Equipamentos"),
        ("Templates (Grupo → Serviços)", "Vincule quais serviços cada grupo de equipamentos deve realizar.",
         c_templates, True, "Admin - Templates"),
        ("Revisão de Manutenção", "Crie uma revisão ativa e gere a matriz de tarefas.",
         c_rev, True, "Admin - Revisões"),
        ("Matriz gerada", "A matriz cria automaticamente as tarefas ao ativar a revisão.",
         c_tasks, True, "Admin - Revisões"),
        ("Permissões por setor", "Configure quais usuários acessam quais departamentos/grupos.",
         0, False, "Admin - Usuários"),
    ]
    total = len(steps_data)
    required_steps = [s for s in steps_data if s[3]]
    done_required  = sum(1 for s in required_steps if s[2] > 0)
    pct_progress   = int((done_required / max(len(required_steps), 1)) * 100)

    # ── Barra de progresso global ─────────────────────────────────────────────
    bar_color = "#38A169" if pct_progress == 100 else ("#D69E2E" if pct_progress >= 50 else "#C53030")
    st.markdown(
        f'''<div style="margin-bottom:20px">
              <div style="display:flex;justify-content:space-between;
                          font-size:0.78rem;color:#8A9BAE;margin-bottom:6px">
                <span>Progresso do setup</span>
                <b style="color:{bar_color}">{done_required}/{len(required_steps)} etapas obrigatórias</b>
              </div>
              <div style="background:rgba(255,255,255,0.07);border-radius:999px;
                          height:8px;overflow:hidden">
                <div style="width:{pct_progress}%;height:100%;border-radius:999px;
                            background:{bar_color};transition:width 0.4s ease"></div>
              </div>
            </div>''',
        unsafe_allow_html=True,
    )

    if pct_progress == 100:
        st.success("✅ Setup completo! O sistema está pronto para operar.")
        st.divider()

    # ── Etapas ────────────────────────────────────────────────────────────────
    col_steps, col_side = st.columns([2, 1])

    with col_steps:
        for i, (title, desc, count, required, page) in enumerate(steps_data, 1):
            _step_card(i, total, title, desc, count, required, page)

    with col_side:
        st.markdown(
            '<div style="font-size:0.68rem;font-weight:600;color:#8A9BAE;'
            'letter-spacing:0.08em;text-transform:uppercase;margin-bottom:12px">'
            'Resumo</div>',
            unsafe_allow_html=True,
        )
        metrics = [
            ("Setores", c_setores), ("Serviços", c_serv),
            ("Grupos", c_grupos), ("Equipamentos", c_eq),
            ("Templates", c_templates), ("Revisões", c_rev),
            ("Tarefas", c_tasks),
        ]
        for label, val in metrics:
            color = "#38A169" if val > 0 else "#8A9BAE"
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;'
                f'padding:5px 0;border-bottom:1px solid rgba(255,255,255,0.05)">'
                f'<span style="font-size:0.8rem;color:#8A9BAE">{_h(label)}</span>'
                f'<b style="font-size:0.8rem;color:{color}">{val}</b></div>',
                unsafe_allow_html=True,
            )

        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

        # Dados de demonstração
        with st.expander("🧪 Dados de demonstração", expanded=False):
            st.caption("Cria setores, grupos, equipamentos e uma revisão demo para testes.")
            confirm = st.checkbox("Confirmo a inserção de dados demo", key="wiz_demo_confirm")
            if st.button("Gerar dados demo", icon=":material/science:",
                         type="primary", use_container_width=True,
                         disabled=not confirm):
                try:
                    with st.spinner("Gerando…"):
                        result = seed_demo_data(str(tenant_id))
                    st.success(
                        f"{result['grupos']} grupos, {result['equipamentos']} equipamentos.")
                    nav.goto("Admin - Revisões")
                except Exception as e:
                    st.error(f"Erro: {e}")
