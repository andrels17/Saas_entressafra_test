import streamlit as st
from collections import defaultdict
import pandas as pd
from src.utils.supabase_helpers import sb_for_user, current_tenant_id, current_role
from src.utils import nav

from src.ui.core.styles import page_header as _ph


def render_admin_templates() -> None:
    _ph("◪", "Templates",
        "Defina quais serviços se aplicam a cada grupo. Controla as colunas da Matriz.")

    role = current_role()
    if role not in ("admin", "superadmin"):
        st.error("Apenas Admin pode gerenciar templates.")
        st.stop()

    tenant_id = current_tenant_id()
    sb = sb_for_user()

    grupos = (
        sb.table("equip_grupos")
        .select("id, nome")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .order("nome")
        .execute()
        .data
    ) or []
    if not grupos:
        st.info("Crie pelo menos um grupo antes de configurar templates.")
        return

    grupo_map = {g["nome"]: g["id"] for g in grupos}

    setores = (
        sb.table("setores")
        .select("id, nome")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .order("nome")
        .execute()
        .data
    ) or []
    if not setores:
        st.warning(
            "Nenhum setor cadastrado ainda. Cadastre setores e serviços para montar templates.")
        return

    servicos = (
        sb.table("servicos")
        .select("id, nome, setor_id")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .order("nome")
        .execute()
        .data
    ) or []
    if not servicos:
        st.warning(
            "Nenhum serviço cadastrado ainda. Cadastre serviços para montar templates.")
        return
    # ---------------------------------------------------------
    # Cobertura (nível gerente): grupos com template + equipamentos ativos
    # ---------------------------------------------------------
    eq_rows = (
        sb.table("equipamentos")
        .select("id, grupo_id, ativo")
        .eq("tenant_id", tenant_id)
        .execute()
        .data
    ) or []
    eq_active_by_gid = defaultdict(int)
    for r in eq_rows:
        if r.get("ativo") and r.get("grupo_id"):
            eq_active_by_gid[r["grupo_id"]] += 1

    tpl_rows_all = (
        sb.table("grupo_servicos")
        .select("grupo_id, servico_id")
        .eq("tenant_id", tenant_id)
        .execute()
        .data
    ) or []
    svc_by_gid = defaultdict(set)
    for r in tpl_rows_all:
        if r.get("grupo_id") and r.get("servico_id"):
            svc_by_gid[r["grupo_id"]].add(r["servico_id"])

    cov_rows = []
    for g in grupos:
        gid = g["id"]
        cov_rows.append(
            {
                "Grupo": g["nome"], "Equip ativos": int(
                    eq_active_by_gid.get(
                        gid, 0)), "Serviços no template": int(
                    len(
                        svc_by_gid.get(
                            gid, set()))), "Coberto": bool(
                                eq_active_by_gid.get(
                                    gid, 0) > 0 and len(
                                        svc_by_gid.get(
                                            gid, set())) > 0), "grupo_id": gid, })
    cov_df = st.session_state.get("_cov_df_cache")
    cov_df = cov_df if isinstance(
        cov_df, type(None)) else cov_df  # no-op (compat)
    cov_df = cov_rows

    covered = sum(1 for r in cov_rows if r["Coberto"])
    # ── Seção: ações em massa ────────────────────────────────────────────────
    with st.expander("Cobertura + ações em massa (recomendado)", expanded=False):
        c1, c2, c3 = st.columns(3)
        c1.metric("Grupos ativos", f"{len(grupos)}")
        c2.metric("Grupos com template",
                  f"{sum(1 for r in cov_rows if r['Serviços no template'] > 0)}")
        c3.metric("Grupos cobertos (template + equip)",
                  f"{covered}/{len(grupos)}")

        df_show = pd.DataFrame(cov_rows).drop(columns=["grupo_id"]).sort_values(
            ["Coberto", "Serviços no template", "Equip ativos"], ascending=[True, True, True])
        st.dataframe(df_show, use_container_width=True, hide_index=True)

        st.markdown("#### Copiar template entre grupos")
        colA, colB, colC = st.columns([1.2, 1.2, 1])
        with colA:
            src_g = st.selectbox("Origem", [g["nome"]
                                 for g in grupos], key="tpl_copy_src")
        with colB:
            dst_g = st.selectbox(
                "Destino", [
                    g["nome"] for g in grupos], key="tpl_copy_dst")
        with colC:
            only_if_empty = st.toggle(
                "Somente se destino estiver vazio",
                value=True,
                key="tpl_copy_only_empty")

        if st.button("Copiar", use_container_width=True):
            src_id = grupo_map.get(src_g)
            dst_id = grupo_map.get(dst_g)
            if not src_id or not dst_id or src_id == dst_id:
                st.error("Selecione origem e destino diferentes.")
            else:
                src_svcs = list(svc_by_gid.get(src_id, set()))
                if not src_svcs:
                    st.error("Origem não tem serviços no template.")
                else:
                    dst_has = len(svc_by_gid.get(dst_id, set())) > 0
                    if only_if_empty and dst_has:
                        st.warning(
                            "Destino já possui template. Desmarque a opção para sobrescrever.")
                    else:
                        # remove existentes e insere os novos
                        try:
                            sb.table("grupo_servicos").delete().eq(
                                "tenant_id", tenant_id).eq(
                                "grupo_id", dst_id).execute()
                            sb.table("grupo_servicos").insert(
                                [{"tenant_id": tenant_id, "grupo_id": dst_id, "servico_id": sid} for sid in src_svcs]
                            ).execute()
                            st.success(
                                "Template copiado. Recarregue a página para atualizar.")
                        except Exception as e:
                            st.error(f"Falha ao copiar: {e}")

        st.markdown("#### Aplicar template por departamento (em massa)")
        deps = (
            sb.table("departamentos")
            .select("id,nome")
            .eq("tenant_id", tenant_id)
            .execute()
            .data
        ) or []
        dep_map = {d["nome"]: d["id"] for d in deps if d.get("id")}
        dep_pick = st.selectbox(
            "Departamento",
            ["—"] +
            list(
                dep_map.keys()),
            key="tpl_mass_dep")
        ref_group = st.selectbox(
            "Usar template do grupo", [
                g["nome"] for g in grupos], key="tpl_mass_ref")
        only_missing = st.toggle(
            "Somente grupos sem template",
            value=True,
            key="tpl_mass_only_missing")
        if st.button("Aplicar no departamento", use_container_width=True):
            if dep_pick == "—":
                st.error("Selecione um departamento.")
            else:
                dep_id = dep_map.get(dep_pick)
                ref_id = grupo_map.get(ref_group)
                ref_svcs = list(svc_by_gid.get(ref_id, set()))
                if not ref_svcs:
                    st.error("Grupo referência não tem template.")
                else:
                    # buscar grupos do depto
                    gs = (
                        sb.table("equip_grupos")
                        .select("id,nome,departamento_id")
                        .eq("tenant_id", tenant_id)
                        .eq("ativo", True)
                        .eq("departamento_id", dep_id)
                        .execute()
                        .data
                    ) or []
                    applied = 0
                    for g in gs:
                        gid = g["id"]
                        if only_missing and len(
                                svc_by_gid.get(gid, set())) > 0:
                            continue
                        try:
                            sb.table("grupo_servicos").delete().eq(
                                "tenant_id", tenant_id).eq(
                                "grupo_id", gid).execute()
                            sb.table("grupo_servicos").insert(
                                [{"tenant_id": tenant_id, "grupo_id": gid, "servico_id": sid} for sid in ref_svcs]
                            ).execute()
                            applied += 1
                        except Exception:
                            continue
                    st.success(
                        f"Aplicado em {applied} grupos do departamento. Recarregue a página.")

    grupo_nome = st.selectbox("Grupo", list(grupo_map.keys()))
    grupo_id = grupo_map[grupo_nome]

    # atuais
    atuais = (
        sb.table("grupo_servicos")
        .select("servico_id")
        .eq("tenant_id", tenant_id)
        .eq("grupo_id", grupo_id)
        .execute()
        .data
    ) or []
    atuais_set = {a["servico_id"] for a in atuais}

    setor_nome = {s["id"]: s["nome"] for s in setores}
    by_setor = defaultdict(list)
    for sv in servicos:
        by_setor[sv["setor_id"]].append(sv)

    # ── Seção: seleção de serviços por setor ────────────────────────────────
    st.markdown("### Seleção de serviços")
    st.caption(
        "Dica: expanda um setor e marque os serviços aplicáveis. Você pode salvar quantas vezes quiser.")

    # estado local do que está selecionado (derivado das checkboxes)
    selecionados = set(atuais_set)

    # UI por setor com checkboxes + ações rápidas
    for sid, lista in by_setor.items():
        setor_label = setor_nome.get(sid, "Setor")
        with st.expander(setor_label, expanded=False):
            cA, cB, _ = st.columns([0.25, 0.25, 0.5])

            # Ações rápidas por setor (operam em session_state)
            mark_key = f"tpl_markall_{grupo_id}_{sid}"
            clear_key = f"tpl_clear_{grupo_id}_{sid}"

            if cA.button(
                "Marcar todos",
                key=mark_key,
                    use_container_width=True):
                for sv in lista:
                    st.session_state[f"tpl_{grupo_id}_{sv['id']}"] = True

            if cB.button("Limpar", key=clear_key, use_container_width=True):
                for sv in lista:
                    st.session_state[f"tpl_{grupo_id}_{sv['id']}"] = False

            for sv in lista:
                key = f"tpl_{grupo_id}_{sv['id']}"
                default = sv["id"] in atuais_set

                # se a key ainda não existe, inicializa
                if key not in st.session_state:
                    st.session_state[key] = default

                checked = st.checkbox(
                    sv["nome"], value=st.session_state[key], key=key)
                if checked:
                    selecionados.add(sv["id"])
                else:
                    selecionados.discard(sv["id"])

    st.divider()
    c1, c2 = st.columns([0.6, 0.4])
    with c1:
        st.metric("Serviços selecionados", len(selecionados))

    with c2:
        if st.button(
            "Salvar template",
            icon=":material/save:",
            type="primary",
                use_container_width=True):
            try:
                # estratégia simples: limpa e reinsere (admin-only)
                sb.table("grupo_servicos").delete().eq(
                    "tenant_id", tenant_id).eq(
                    "grupo_id", grupo_id).execute()

                if selecionados:
                    payload = [
                        {
                            "tenant_id": tenant_id,
                            "grupo_id": grupo_id,
                            "servico_id": sid_sel,
                            "obrigatorio": True,
                        }
                        for sid_sel in selecionados
                    ]
                    sb.table("grupo_servicos").insert(payload).execute()

                st.success(
                    f"Template salvo. ({
                        len(selecionados)} serviço(s) vinculados)")
                nav.rerun_keep_menu()
            except Exception as e:
                st.error(f"Erro ao salvar template: {e}")
