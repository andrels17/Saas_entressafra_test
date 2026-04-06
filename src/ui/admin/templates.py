"""Admin — Templates.

Melhorias UI/UX v2:
- Seletor de grupo com card de status (cobertura) embutido
- Painel de cobertura sempre visível (não escondido em expander)
- Checkboxes organizados em 2 colunas por setor (mais denso, menos scroll)
- Contador de selecionados atualizado em tempo real com barra de progresso
- Ações em massa em expander separado e bem rotulado
- Copy de template com preview do que será copiado
- Feedback mais claro em todas as ações
"""
from __future__ import annotations
from html import escape as _h

from collections import defaultdict

import pandas as pd
import streamlit as st
from src.utils.supabase_helpers import sb_for_user, current_tenant_id, current_role
from src.utils import nav
from src.ui.core.styles import page_header as _ph
from src.ui.admin_components.utils import inject_enterprise_css


# ── CSS ───────────────────────────────────────────────────────────────────────

def _inject_css() -> None:
    inject_enterprise_css()
    st.markdown("""
<style>
.tpl-coverage-card {
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 14px;
    padding: 14px 18px;
    margin-bottom: 16px;
}
.tpl-coverage-row {
    display:flex; align-items:center; justify-content:space-between;
    padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.05);
}
.tpl-coverage-row:last-child { border-bottom: none; }
.tpl-group-name { font-weight:600; font-size:0.93rem; color:#F1F5F9; }
.tpl-meta { font-size:0.80rem; color:#94A3B8; }
.tpl-badge-ok {
    padding:2px 9px; border-radius:999px; font-size:11px; font-weight:700;
    background:rgba(34,197,94,.15); color:#4ADE80; border:1px solid rgba(34,197,94,.30);
}
.tpl-badge-warn {
    padding:2px 9px; border-radius:999px; font-size:11px; font-weight:700;
    background:rgba(245,158,11,.15); color:#FCD34D; border:1px solid rgba(245,158,11,.30);
}
.tpl-badge-err {
    padding:2px 9px; border-radius:999px; font-size:11px; font-weight:700;
    background:rgba(239,68,68,.14); color:#FCA5A5; border:1px solid rgba(239,68,68,.28);
}
.tpl-section-header {
    font-size:0.78rem; font-weight:700; color:#64748B; text-transform:uppercase;
    letter-spacing:.09em; margin: 18px 0 8px 0;
}
.tpl-setor-label {
    font-size:0.88rem; font-weight:700; color:#CBD5E1; margin-bottom:4px;
}
.tpl-progress-bar-bg {
    background: rgba(255,255,255,0.08); border-radius:999px; height:6px; margin:6px 0 12px 0;
}
.tpl-progress-bar-fill {
    border-radius:999px; height:6px; background: linear-gradient(90deg,#3B82F6,#60A5FA);
    transition: width .3s ease;
}
</style>
""", unsafe_allow_html=True)


def _cov_badge(covered: bool, has_equip: bool, has_svc: bool) -> str:
    if covered:
        return '<span class="tpl-badge-ok">✓ Coberto</span>'
    if not has_equip and not has_svc:
        return '<span class="tpl-badge-err">Sem equip. e template</span>'
    if not has_equip:
        return '<span class="tpl-badge-warn">Sem equipamentos</span>'
    return '<span class="tpl-badge-warn">Sem template</span>'


# ── Painel de cobertura ───────────────────────────────────────────────────────

def _render_coverage(grupos, eq_active_by_gid, svc_by_gid) -> None:
    cov_rows = []
    for g in grupos:
        gid = g["id"]
        n_eq = int(eq_active_by_gid.get(gid, 0))
        n_svc = int(len(svc_by_gid.get(gid, set())))
        covered = n_eq > 0 and n_svc > 0
        cov_rows.append({"g": g, "n_eq": n_eq, "n_svc": n_svc, "covered": covered})

    covered_count = sum(1 for r in cov_rows if r["covered"])
    total = len(grupos)
    pct = int((covered_count / max(total, 1)) * 100)

    col1, col2, col3 = st.columns(3)
    col1.metric("Grupos ativos", total)
    col2.metric("Com template", sum(1 for r in cov_rows if r["n_svc"] > 0))
    col3.metric("Cobertos", f"{covered_count}/{total}")

    st.markdown(
        f'<div class="tpl-progress-bar-bg">'
        f'<div class="tpl-progress-bar-fill" style="width:{pct}%"></div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    busca_cov = st.text_input(
        "filtro cobertura", placeholder="🔍  Filtrar grupos…",
        label_visibility="collapsed", key="tpl_cov_busca",
    )

    st.markdown('<div class="tpl-coverage-card">', unsafe_allow_html=True)
    for r in cov_rows:
        g = r["g"]
        if busca_cov and busca_cov.lower() not in g["nome"].lower():
            continue
        st.markdown(
            f'<div class="tpl-coverage-row">'
            f'<div><span class="tpl-group-name">{_h(str(g["nome"] or ""))}</span>'
            f'<br><span class="tpl-meta">{r["n_eq"]} equip. · {r["n_svc"]} serviços</span></div>'
            f'{_cov_badge(r["covered"], r["n_eq"] > 0, r["n_svc"] > 0)}'
            f'</div>',
            unsafe_allow_html=True,
        )
    st.markdown('</div>', unsafe_allow_html=True)


# ── Ações em massa ────────────────────────────────────────────────────────────

def _render_mass_actions(sb, tenant_id, grupos, grupo_map, svc_by_gid) -> None:
    with st.expander("⚡ Ações em massa", expanded=False):
        st.markdown("#### Copiar template entre grupos")
        st.caption("Copia todos os serviços do grupo origem para o destino.")
        colA, colB, colC = st.columns([1.5, 1.5, 1])
        with colA:
            src_g = st.selectbox("Origem", [g["nome"] for g in grupos], key="tpl_copy_src")
        with colB:
            dst_g = st.selectbox("Destino", [g["nome"] for g in grupos], key="tpl_copy_dst")
        with colC:
            only_if_empty = st.toggle("Apenas se destino vazio", value=True, key="tpl_copy_only_empty")

        src_id = grupo_map.get(src_g)
        dst_id = grupo_map.get(dst_g)
        src_svcs = list(svc_by_gid.get(src_id, set())) if src_id else []
        if src_svcs:
            st.caption(f"📋 O grupo **{src_g}** possui **{len(src_svcs)}** serviço(s) no template.")
        else:
            st.caption(f"⚠️ O grupo **{src_g}** não tem template definido.")

        if st.button("Copiar template →", key="tpl_copy_btn", use_container_width=True, type="primary"):
            if not src_id or not dst_id or src_id == dst_id:
                st.error("Selecione grupos de origem e destino diferentes.")
            elif not src_svcs:
                st.error("Grupo origem não tem serviços no template.")
            else:
                dst_has = len(svc_by_gid.get(dst_id, set())) > 0
                if only_if_empty and dst_has:
                    st.warning("Destino já possui template. Desmarque a opção para sobrescrever.")
                else:
                    try:
                        sb.table("grupo_servicos").delete().eq("tenant_id", tenant_id).eq("grupo_id", dst_id).execute()
                        sb.table("grupo_servicos").insert(
                            [{"tenant_id": tenant_id, "grupo_id": dst_id, "servico_id": sid} for sid in src_svcs]
                        ).execute()
                        st.success(f"✅ Template copiado! {len(src_svcs)} serviço(s) vinculados ao grupo **{dst_g}**.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Falha ao copiar: {e}")

        st.divider()
        st.markdown("#### Aplicar template por departamento")
        st.caption("Aplica o template de um grupo referência a todos os grupos de um departamento.")
        deps = (
            sb.table("departamentos").select("id,nome")
            .eq("tenant_id", tenant_id).execute().data
        ) or []
        dep_map = {d["nome"]: d["id"] for d in deps if d.get("id")}

        col_dep, col_ref, col_miss = st.columns([1.5, 1.5, 1])
        with col_dep:
            dep_pick = st.selectbox("Departamento", ["—"] + list(dep_map.keys()), key="tpl_mass_dep")
        with col_ref:
            ref_group = st.selectbox("Usar template do grupo", [g["nome"] for g in grupos], key="tpl_mass_ref")
        with col_miss:
            only_missing = st.toggle("Só grupos sem template", value=True, key="tpl_mass_only_missing")

        if st.button("Aplicar no departamento →", key="tpl_mass_btn", use_container_width=True):
            if dep_pick == "—":
                st.error("Selecione um departamento.")
            else:
                dep_id = dep_map.get(dep_pick)
                ref_id = grupo_map.get(ref_group)
                ref_svcs = list(svc_by_gid.get(ref_id, set()))
                if not ref_svcs:
                    st.error("Grupo referência não tem template.")
                else:
                    gs = (
                        sb.table("equip_grupos").select("id,nome")
                        .eq("tenant_id", tenant_id).eq("ativo", True)
                        .eq("departamento_id", dep_id).execute().data
                    ) or []
                    applied = 0
                    for g in gs:
                        gid = g["id"]
                        if only_missing and len(svc_by_gid.get(gid, set())) > 0:
                            continue
                        try:
                            sb.table("grupo_servicos").delete().eq("tenant_id", tenant_id).eq("grupo_id", gid).execute()
                            sb.table("grupo_servicos").insert(
                                [{"tenant_id": tenant_id, "grupo_id": gid, "servico_id": sid} for sid in ref_svcs]
                            ).execute()
                            applied += 1
                        except Exception:
                            continue
                    st.success(f"✅ Template aplicado em **{applied}** grupo(s) do departamento **{dep_pick}**.")
                    st.rerun()


# ── Seleção de serviços ───────────────────────────────────────────────────────

def _render_service_selection(sb, tenant_id, grupo_id, grupo_nome, setores, servicos, atuais_set) -> None:
    setor_nome = {s["id"]: s["nome"] for s in setores}
    by_setor: dict = defaultdict(list)
    for sv in servicos:
        by_setor[sv["setor_id"]].append(sv)

    total_servicos = len(servicos)
    selecionados: set = set(atuais_set)

    st.markdown('<div class="tpl-section-header">Serviços do template</div>', unsafe_allow_html=True)
    st.caption("Expanda um setor e marque os serviços aplicáveis a este grupo.")

    for sid, lista in sorted(by_setor.items(), key=lambda x: setor_nome.get(x[0], "")):
        setor_label = setor_nome.get(sid, "Setor")
        sel_count = sum(1 for sv in lista if sv["id"] in selecionados)
        header_label = f"**{setor_label}** — {sel_count}/{len(lista)} selecionado(s)"

        with st.expander(header_label, expanded=False):
            # Ações rápidas
            cA, cB, _ = st.columns([1, 1, 3])
            if cA.button("☑ Todos", key=f"tpl_markall_{grupo_id}_{sid}", use_container_width=True):
                for sv in lista:
                    st.session_state[f"tpl_{grupo_id}_{sv['id']}"] = True
                st.rerun()
            if cB.button("☐ Limpar", key=f"tpl_clear_{grupo_id}_{sid}", use_container_width=True):
                for sv in lista:
                    st.session_state[f"tpl_{grupo_id}_{sv['id']}"] = False
                st.rerun()

            # Checkboxes em 2 colunas
            col_pairs = [lista[i:i+2] for i in range(0, len(lista), 2)]
            for pair in col_pairs:
                cols = st.columns(2)
                for col, sv in zip(cols, pair):
                    key = f"tpl_{grupo_id}_{sv['id']}"
                    if key not in st.session_state:
                        st.session_state[key] = sv["id"] in atuais_set
                    checked = col.checkbox(sv["nome"], value=st.session_state[key], key=key)
                    if checked:
                        selecionados.add(sv["id"])
                    else:
                        selecionados.discard(sv["id"])

    # Rodapé com contador + salvar
    st.divider()
    pct_sel = int((len(selecionados) / max(total_servicos, 1)) * 100)
    st.markdown(
        f'<div class="tpl-progress-bar-bg">'
        f'<div class="tpl-progress-bar-fill" style="width:{pct_sel}%"></div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    col_m, col_s = st.columns([2, 1])
    with col_m:
        st.markdown(
            f'<div style="color:#94A3B8;font-size:0.86rem;padding-top:6px">'
            f'<strong style="color:#F1F5F9;font-size:1.1rem">{len(selecionados)}</strong>'
            f' / {total_servicos} serviço(s) selecionado(s) — <strong>{pct_sel}%</strong>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with col_s:
        if st.button("💾 Salvar template", key="tpl_save_btn", type="primary", use_container_width=True):
            try:
                sb.table("grupo_servicos").delete().eq("tenant_id", tenant_id).eq("grupo_id", grupo_id).execute()
                if selecionados:
                    sb.table("grupo_servicos").insert([
                        {"tenant_id": tenant_id, "grupo_id": grupo_id, "servico_id": sid_sel, "obrigatorio": True}
                        for sid_sel in selecionados
                    ]).execute()
                st.success(f"✅ Template salvo — {len(selecionados)} serviço(s) vinculado(s) ao grupo **{grupo_nome}**.")
                nav.rerun_keep_menu()
            except Exception as e:
                st.error(f"Erro ao salvar template: {e}")


# ── Entry point ───────────────────────────────────────────────────────────────

def render_admin_templates() -> None:
    _ph("◪", "Templates",
        "Defina quais serviços se aplicam a cada grupo. Controla as colunas da Matriz.")

    role = current_role()
    if role not in ("admin", "superadmin"):
        st.error("🔒 Apenas Admin pode gerenciar templates.")
        st.stop()

    _inject_css()

    tenant_id = current_tenant_id()
    sb = sb_for_user()

    # ── Carregar dados base ───────────────────────────────────────────────────
    grupos = (
        sb.table("equip_grupos").select("id, nome")
        .eq("tenant_id", tenant_id).eq("ativo", True).order("nome").execute().data
    ) or []
    if not grupos:
        st.info("ℹ️ Crie pelo menos um grupo antes de configurar templates.")
        return

    setores = (
        sb.table("setores").select("id, nome")
        .eq("tenant_id", tenant_id).eq("ativo", True).order("nome").execute().data
    ) or []
    if not setores:
        st.warning("⚠️ Nenhum setor cadastrado. Cadastre setores e serviços para montar templates.")
        return

    servicos = (
        sb.table("servicos").select("id, nome, setor_id")
        .eq("tenant_id", tenant_id).eq("ativo", True).order("nome").execute().data
    ) or []
    if not servicos:
        st.warning("⚠️ Nenhum serviço cadastrado. Cadastre serviços para montar templates.")
        return

    grupo_map = {g["nome"]: g["id"] for g in grupos}

    # Dados de cobertura
    eq_rows = (
        sb.table("equipamentos").select("id, grupo_id, ativo")
        .eq("tenant_id", tenant_id).execute().data
    ) or []
    eq_active_by_gid: dict = defaultdict(int)
    for r in eq_rows:
        if r.get("ativo") and r.get("grupo_id"):
            eq_active_by_gid[r["grupo_id"]] += 1

    tpl_rows_all = (
        sb.table("grupo_servicos").select("grupo_id, servico_id")
        .eq("tenant_id", tenant_id).execute().data
    ) or []
    svc_by_gid: dict = defaultdict(set)
    for r in tpl_rows_all:
        if r.get("grupo_id") and r.get("servico_id"):
            svc_by_gid[r["grupo_id"]].add(r["servico_id"])

    # ── Layout: duas abas principais ─────────────────────────────────────────
    tab_edit, tab_cov, tab_mass = st.tabs([
        "✏️  Editar template",
        "📊  Cobertura",
        "⚡  Ações em massa",
    ])

    with tab_cov:
        st.markdown('<div class="tpl-section-header">Visão de cobertura por grupo</div>', unsafe_allow_html=True)
        _render_coverage(grupos, eq_active_by_gid, svc_by_gid)

    with tab_mass:
        _render_mass_actions(sb, tenant_id, grupos, grupo_map, svc_by_gid)

    with tab_edit:
        st.markdown('<div class="tpl-section-header">Selecionar grupo</div>', unsafe_allow_html=True)

        # Seletor de grupo com indicador de status embutido
        col_sel, col_status = st.columns([2, 1])
        with col_sel:
            grupo_nome = st.selectbox(
                "grupo", list(grupo_map.keys()),
                label_visibility="collapsed", key="tpl_grupo_sel",
            )
        grupo_id = grupo_map[grupo_nome]
        n_svc_atual = len(svc_by_gid.get(grupo_id, set()))
        n_eq_atual = int(eq_active_by_gid.get(grupo_id, 0))
        covered = n_eq_atual > 0 and n_svc_atual > 0
        with col_status:
            badge = _cov_badge(covered, n_eq_atual > 0, n_svc_atual > 0)
            st.markdown(
                f'<div style="padding-top:8px">{badge}'
                f'<span style="font-size:0.80rem;color:#64748B;margin-left:6px">'
                f'{n_eq_atual} equip. · {n_svc_atual} serv.</span></div>',
                unsafe_allow_html=True,
            )

        # Carregar template atual
        atuais = (
            sb.table("grupo_servicos").select("servico_id")
            .eq("tenant_id", tenant_id).eq("grupo_id", grupo_id).execute().data
        ) or []
        atuais_set = {a["servico_id"] for a in atuais}

        _render_service_selection(sb, tenant_id, grupo_id, grupo_nome, setores, servicos, atuais_set)
