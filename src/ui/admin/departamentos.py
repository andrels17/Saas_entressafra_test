import streamlit as st
from src.ui.core.design_system import inject_design_system_css
from src.ui.admin_components.layout import admin_block, admin_divider
from src.ui.admin_components.utils import inject_enterprise_css, clamp, pager, safe_rerun, norm_name
import re
import unicodedata

from src.utils.supabase_helpers import sb_for_user, current_tenant_id, current_role

from src.ui.core.styles import page_header as _ph


# _inject_enterprise_css → moved to admin_components.utils


# clamp → clamp from admin_components.utils


# pager → pager from admin_components.utils


# safe_rerun → safe_rerun from admin_components.utils


# norm_name → norm_name from admin_components.utils


def _render_limpeza_departamentos(sb, tenant_id: str):
    """Limpeza focada em Departamentos (dedupe + remover órfãos)."""
    with st.expander("🧹 Limpeza de Departamentos (anti-duplicidade)", expanded=False):
        st.caption(
            "Deduplicar departamentos (ex.: 'Tratores' vs 'TRATORES' vs 'Tratores ') e "
            "remover departamentos vazios (sem grupos)."
        )

        deps = (
            sb.table("departamentos")
            .select("id,nome,ativo,created_at")
            .eq("tenant_id", tenant_id)
            .execute()
            .data
        ) or []

        try:
            grupos = (
                sb.table("equip_grupos")
                .select("id,departamento_id")
                .eq("tenant_id", tenant_id)
                .execute()
                .data
            ) or []
        except Exception:
            grupos = []

        dep_to_group_cnt: dict[str, int] = {}
        for g in grupos:
            did = g.get("departamento_id")
            if did:
                dep_to_group_cnt[did] = dep_to_group_cnt.get(did, 0) + 1

        buckets: dict[str, list[dict]] = {}
        for d in deps:
            key = norm_name(str(d.get("nome", "")))
            if not key:
                continue
            buckets.setdefault(key, []).append(d)
        dup_buckets = {k: v for k, v in buckets.items() if len(v) > 1}

        c1, c2, c3 = st.columns(3, gap="small")
        c1.metric("Departamentos", len(deps))
        c2.metric("Duplicados", sum(len(v) for v in dup_buckets.values()))
        c3.metric("Sem grupos", sum(1 for d in deps if dep_to_group_cnt.get(d.get("id"), 0) == 0))

        if dup_buckets:
            st.markdown("**Amostra de duplicidades:**")
            sample_rows = []
            for k, v in list(dup_buckets.items())[:10]:
                nomes = ", ".join([str(x.get("nome")) for x in v][:6])
                sample_rows.append({"chave": k, "itens": len(v), "nomes": nomes})
            st.dataframe(sample_rows, use_container_width=True, hide_index=True)
        else:
            st.info("Nenhuma duplicidade óbvia encontrada.")

        admin_divider()

        st.markdown("#### Deduplicar")
        st.caption(
            "Mantém 1 departamento (preferindo **ativo** e com **mais grupos**), reaponta os grupos e "
            "tenta apagar os duplicados. Se o banco bloquear, desativa."
        )
        confirm_txt = st.text_input("Digite DEDUPLICAR", value="", key="dep_dedupe_confirm")
        if st.button(
            "Deduplicar agora",
            type="primary",
            use_container_width=True,
            disabled=(confirm_txt.strip().upper() != "DEDUPLICAR"),
            key="dep_dedupe_btn",
        ):
            changed = 0
            for _, items in dup_buckets.items():
                def score(d):
                    did = d.get("id")
                    return (
                        1 if d.get("ativo") else 0,
                        dep_to_group_cnt.get(did, 0),
                        # created_at menor (mais antigo) costuma ser o "principal"
                        "9999" if d.get("created_at") is None else str(d.get("created_at")),
                    )

                items_sorted = sorted(items, key=score, reverse=True)
                canon = items_sorted[0]
                canon_id = canon.get("id")
                for d in items_sorted[1:]:
                    did = d.get("id")
                    if not did or did == canon_id:
                        continue

                    try:
                        sb.table("equip_grupos").update({"departamento_id": canon_id}).eq("tenant_id", tenant_id).eq("departamento_id", did).execute()
                    except Exception as e:
                        st.error(f"Falha ao atualizar equip_grupos.departamento_id ({did} -> {canon_id}): {e}")
                        continue

                    # se existir equipamentos.departamento_id, reaponta (senão ignora)
                    try:
                        sb.table("equipamentos").update({"departamento_id": canon_id}).eq("tenant_id", tenant_id).eq("departamento_id", did).execute()
                    except Exception:
                        pass

                    try:
                        sb.table("departamentos").delete().eq("tenant_id", tenant_id).eq("id", did).execute()
                    except Exception:
                        try:
                            sb.table("departamentos").update({"ativo": False}).eq("tenant_id", tenant_id).eq("id", did).execute()
                        except Exception as e:
                            st.error(f"Falha ao desativar duplicado ({did}): {e}")
                            continue

                    changed += 1

            st.success(f"Deduplicação concluída. Itens processados: {changed}.")
            safe_rerun()

        admin_divider()

        st.markdown("#### Remover departamentos vazios")
        mode = st.radio("Ação", ["Desativar", "Apagar"], horizontal=True, key="dep_empty_mode")
        confirm2 = st.text_input("Digite LIMPAR", value="", key="dep_empty_confirm")
        if st.button(
            "Limpar vazios",
            use_container_width=True,
            disabled=(confirm2.strip().upper() != "LIMPAR"),
            key="dep_empty_btn",
        ):
            empty_ids = [d.get("id") for d in deps if dep_to_group_cnt.get(d.get("id"), 0) == 0 and d.get("id")]
            if not empty_ids:
                st.info("Nenhum departamento vazio encontrado.")
            else:
                ok = 0
                for did in empty_ids:
                    try:
                        if mode == "Apagar":
                            sb.table("departamentos").delete().eq("tenant_id", tenant_id).eq("id", did).execute()
                        else:
                            sb.table("departamentos").update({"ativo": False}).eq("tenant_id", tenant_id).eq("id", did).execute()
                        ok += 1
                    except Exception as e:
                        st.error(f"Falha ao limpar departamento {did}: {e}")
                st.success(f"Limpeza concluída. Itens afetados: {ok}.")
                safe_rerun()




def _render_limpeza_total_departamentos(sb, tenant_id: str):
    """Limpeza 'de verdade' para departamentos: desativar/apagar em massa.

    Observação: para **apagar**, primeiro desvinculamos equip_grupos.departamento_id (e best-effort em equipamentos.departamento_id).
    """
    with st.expander("🧼 Limpeza completa de Departamentos", expanded=False):
        st.caption(
            "Use isso para **limpar de verdade** (em massa) a tabela de departamentos. "
            "Recomendado fazer backup antes."
        )

        deps = (
            sb.table("departamentos")
            .select("id,nome,ativo,created_at")
            .eq("tenant_id", tenant_id)
            .execute()
            .data
        ) or []

        c1, c2, c3 = st.columns(3, gap="small")
        c1.metric("Departamentos", len(deps))
        c2.metric("Ativos", sum(1 for d in deps if d.get("ativo")))
        c3.metric("Inativos", sum(1 for d in deps if not d.get("ativo")))

        st.markdown("#### Backup rápido")
        if deps:
            try:
                import pandas as _pd
                df = _pd.DataFrame(deps)
                csv = df.to_csv(index=False).encode("utf-8")
            except Exception:
                # fallback simples
                import csv as _csv
                import io as _io
                buf = _io.StringIO()
                w = _csv.DictWriter(buf, fieldnames=sorted({k for r in deps for k in r.keys()}))
                w.writeheader()
                for r in deps:
                    w.writerow(r)
                csv = buf.getvalue().encode('utf-8')

            st.download_button(
                "Baixar CSV de departamentos", icon=":material/download:",
                data=csv,
                file_name="departamentos_backup.csv",
                mime="text/csv",
                use_container_width=True,
                key="dep_backup_csv_btn",
            )
        else:
            st.info("Não há departamentos para exportar.")

        admin_divider()

        st.markdown("#### Limpar em massa")
        alvo = st.radio("O que limpar", ["Somente inativos", "Todos (inclusive ativos)"], horizontal=True, key="dep_mass_target")
        acao = st.radio("Como limpar", ["Desativar (soft)", "Apagar definitivamente (hard)"], horizontal=True, key="dep_mass_mode")

        st.warning(
            "⚠️ **Apagar definitivamente** pode falhar se existirem vínculos protegidos por Foreign Key. "
            "Nesses casos, a operação é interrompida e o erro é mostrado."
        )

        confirm = st.text_input("Digite LIMPAR DEPARTAMENTOS", value="", key="dep_mass_confirm")
        confirm_ck = st.checkbox("Estou ciente e quero executar", value=False, key="dep_mass_ack")

        if st.button(
            "Executar limpeza",
            type="primary",
            use_container_width=True,
            disabled=(confirm.strip().upper() != "LIMPAR DEPARTAMENTOS" or not confirm_ck),
            key="dep_mass_btn",
        ):
            ids = []
            for d in deps:
                if alvo.startswith("Somente") and d.get("ativo"):
                    continue
                if d.get("id"):
                    ids.append(d["id"])

            if not ids:
                st.info("Nada para limpar com os critérios selecionados.")
                return

            ok = 0
            for did in ids:
                try:
                    if acao.startswith("Desativar"):
                        sb.table("departamentos").update({"ativo": False}).eq("tenant_id", tenant_id).eq("id", did).execute()
                        ok += 1
                        continue

                    # HARD DELETE: desvincula e apaga
                    try:
                        sb.table("equip_grupos").update({"departamento_id": None}).eq("tenant_id", tenant_id).eq("departamento_id", did).execute()
                    except Exception as e:
                        st.error(f"Falha ao desvincular grupos do departamento {did}: {e}")
                        raise

                    # Best-effort: se existir equipamentos.departamento_id
                    try:
                        sb.table("equipamentos").update({"departamento_id": None}).eq("tenant_id", tenant_id).eq("departamento_id", did).execute()
                    except Exception:
                        pass

                    sb.table("departamentos").delete().eq("tenant_id", tenant_id).eq("id", did).execute()
                    ok += 1
                except Exception as e:
                    st.error(f"Erro ao limpar departamento {did}: {e}")
                    break

            st.success(f"Limpeza concluída. Itens afetados: {ok}.")
            safe_rerun()


def render_admin_departamentos():
    _ph("◩", "Departamentos", "Nível acima de grupos — organize seus grupos por departamento (ex.: Tratores, Caminhões, Colhedoras).")
    _inject_enterprise_css()

    role = current_role()
    if role not in ("admin", "superadmin"):
        st.error("Apenas Admin pode gerenciar departamentos.")
        st.stop()

    tenant_id = current_tenant_id()
    sb = sb_for_user()

    # Verifica se a tabela existe (evita quebrar o app antes da migração SQL)
    try:
        _ = sb.table("departamentos").select("id").eq("tenant_id", tenant_id).limit(1).execute()
    except Exception:
        st.warning(
            "A tabela **departamentos** ainda não existe no banco. "
            "Rode o SQL `sql/etapa9_departamentos.sql` no Supabase e recarregue esta tela."
        )
        return


    tab_manage, tab_clean = st.tabs(["📋 Gerenciar", "🧹 Limpeza"])

    with tab_clean:
        # Limpeza (anti-duplicidade / vazios)
        _render_limpeza_departamentos(sb, tenant_id)

        # Limpeza completa (massa)
        _render_limpeza_total_departamentos(sb, tenant_id)

    with tab_manage:
        admin_block("Criar departamento", "Cadastre departamentos com nomes padronizados.")
        with st.form("create_dep"):
            nome = st.text_input("Nome", placeholder="Ex.: Tratores")
            submitted = st.form_submit_button("Criar", use_container_width=True)

        if submitted:
            nn = (nome or "").strip()
            if not nn:
                st.warning("Informe um nome.")
                st.stop()
            try:
                sb.table("departamentos").insert({"tenant_id": tenant_id, "nome": nn, "ativo": True}).execute()
                st.success("Departamento criado.")
                safe_rerun()
            except Exception as e:
                st.error(f"Erro ao criar: {e}")

        admin_divider()

        # ------------------------------
        # Lista (com busca + paginação)
        # ------------------------------
        admin_block("Lista de departamentos", "Busque, filtre e edite os registros cadastrados.")
        f1, f2, f3, f4 = st.columns([0.46, 0.18, 0.18, 0.18], gap="small")
        with f1:
            dep_search = st.text_input("Buscar", placeholder="Buscar por nome…", key="dep_search")
        with f2:
            only_active = st.toggle("Só ativos", value=True, key="dep_only_active")
        with f3:
            sort_mode = st.selectbox(
                "Ordenar",
                ["A–Z", "Z–A", "Ativos primeiro", "Inativos primeiro", "Mais recentes", "Mais antigos"],
                index=0,
                key="dep_sort_mode",
            )
        with f4:
            page_size = st.selectbox("Por página", [10, 20, 50], index=0, key="dep_page_size")

        q = sb.table("departamentos").select("id,nome,ativo,created_at").eq("tenant_id", tenant_id).order("nome")
        if only_active:
            q = q.eq("ativo", True)
        if dep_search:
            # ilike é o melhor (faz case-insensitive) — se não existir no client, cai no filtro local.
            try:
                q = q.ilike("nome", f"%{dep_search.strip()}%")
            except Exception:
                pass

        deps = q.execute().data or []
        if dep_search:
            ss = dep_search.strip().lower()
            deps = [d for d in deps if ss in str(d.get("nome", "")).lower()]

        # Ordenação (cliente) — garante opções como "mais recentes"
        try:
            sm = sort_mode
        except Exception:
            sm = "A–Z"
        if sm == "A–Z":
            deps = sorted(deps, key=lambda x: (str(x.get("nome", "")).lower()))
        elif sm == "Z–A":
            deps = sorted(deps, key=lambda x: (str(x.get("nome", "")).lower()), reverse=True)
        elif sm == "Ativos primeiro":
            deps = sorted(deps, key=lambda x: (0 if x.get("ativo") else 1, str(x.get("nome", "")).lower()))
        elif sm == "Inativos primeiro":
            deps = sorted(deps, key=lambda x: (0 if not x.get("ativo") else 1, str(x.get("nome", "")).lower()))
        elif sm == "Mais recentes":
            deps = sorted(deps, key=lambda x: str(x.get("created_at", "")), reverse=True)
        elif sm == "Mais antigos":
            deps = sorted(deps, key=lambda x: str(x.get("created_at", "")))

        if not deps:
            st.info("Nenhum departamento encontrado.")
            return

        # Pré-cálculos (best-effort) para os badges (grupos/equipamentos)
        dept_group_count: dict[str, int] = {}
        try:
            rows = (
                sb.table("equip_grupos")
                .select("id,departamento_id")
                .eq("tenant_id", tenant_id)
                .execute()
                .data
            ) or []
            for r in rows:
                did = r.get("departamento_id")
                if did:
                    dept_group_count[did] = dept_group_count.get(did, 0) + 1
        except Exception:
            dept_group_count = {}

        dept_equip_count: dict[str, int] = {}
        try:
            # Conta via join indireto: equipamentos -> grupo -> departamento
            grupos = (
                sb.table("equip_grupos")
                .select("id,departamento_id")
                .eq("tenant_id", tenant_id)
                .execute()
                .data
            ) or []
            grp_to_dep = {g["id"]: g.get("departamento_id") for g in grupos if g.get("id")}
            eq_rows = (
                sb.table("equipamentos")
                .select("id,grupo_id")
                .eq("tenant_id", tenant_id)
                .execute()
                .data
            ) or []
            for e in eq_rows:
                gid = e.get("grupo_id")
                did = grp_to_dep.get(gid)
                if did:
                    dept_equip_count[did] = dept_equip_count.get(did, 0) + 1
        except Exception:
            dept_equip_count = {}


        # KPIs rápidos
        try:
            total_grupos = int(sum(dept_group_count.values()))
        except Exception:
            total_grupos = 0
        try:
            total_equip = int(sum(dept_equip_count.values()))
        except Exception:
            total_equip = 0

        k1, k2, k3 = st.columns(3, gap="small")
        k1.metric("Departamentos", len(deps))
        k2.metric("Grupos", total_grupos)
        k3.metric("Equipamentos", total_equip)

        st.caption(f"Total: **{len(deps)}**")
        page_idx, _ = pager("deps", total=len(deps), page_size=int(page_size))
        start = page_idx * int(page_size)
        end = start + int(page_size)
        deps_page = deps[start:end]

        for d in deps_page:
            did = d["id"]
            n_grupos = dept_group_count.get(did, 0)
            n_equip = dept_equip_count.get(did, 0)        # Cabeçalho compacto (label) + card enterprise dentro do expander
            with st.expander(f"{d['nome']}", expanded=False):
                status_txt = "ATIVO" if d.get("ativo") else "INATIVO"
                status_cls = "badge-active" if d.get("ativo") else "badge-inactive"
                st.markdown(
                    f"""
    <div class='card-enterprise'>
      <div style='display:flex; align-items:flex-start; justify-content:space-between; gap:10px;'>
        <div>
          <div style='font-size:16px; font-weight:800; margin-bottom:6px;'>{d['nome']}</div>
          <div class='small-muted'>ID: {did}</div>
        </div>
        <div style='text-align:right; white-space:nowrap;'>
          <span class='badge {status_cls}'>{status_txt}</span>
          <span class='badge badge-neutral'>{n_grupos} grupos</span>
          <span class='badge badge-neutral'>{n_equip} equipamentos</span>
        </div>
      </div>
    </div>
    """,
                    unsafe_allow_html=True,
                )

                # ------------------------------
                # Ações do departamento
                # ------------------------------
                c1, c2 = st.columns([0.62, 0.38], gap="small")
                with c1:
                    st.caption(f"Ativo: {'Sim' if d.get('ativo') else 'Não'}")
                with c2:
                    novo_nome = st.text_input("Renomear", value=d["nome"], key=f"dep_rename_{did}")

                a1, a2, a3 = st.columns([1, 1, 1], gap="small")
                with a1:
                    if st.button("Salvar", icon=":material/save:", key=f"dep_save_{did}", use_container_width=True):
                        nn = (novo_nome or "").strip()
                        if not nn:
                            st.warning("Nome inválido.")
                            st.stop()
                        try:
                            sb.table("departamentos").update({"nome": nn}).eq("tenant_id", tenant_id).eq("id", did).execute()
                            st.toast("✓ Atualizado", icon=":material/check_circle:")
                            safe_rerun()
                        except Exception as e:
                            st.error(f"Erro ao salvar: {e}")
                with a2:
                    label_btn = "Desativar" if d.get("ativo") else "Ativar"
                    if st.button(label_btn, key=f"dep_toggle_{did}", use_container_width=True):
                        try:
                            sb.table("departamentos").update({"ativo": (not d.get("ativo"))}).eq("tenant_id", tenant_id).eq("id", did).execute()
                            st.toast("✓ Ok", icon=":material/check_circle:")
                            safe_rerun()
                        except Exception as e:
                            st.error(f"Erro: {e}")
                with a3:
                    with st.popover("Excluir", icon=":material/delete:", help="Remover departamento permanentemente"):
                        st.caption("Remove o departamento e **desvincula** os grupos associados (departamento_id vira NULL).")
                        confirm = st.checkbox("Confirmo excluir", value=False, key=f"dep_del_ok_{did}")
                        if st.button("Apagar agora", type="primary", use_container_width=True, disabled=not confirm, key=f"dep_del_{did}"):
                            try:
                                sb.table("equip_grupos").update({"departamento_id": None}).eq("tenant_id", tenant_id).eq("departamento_id", did).execute()
                            except Exception:
                                pass
                            try:
                                sb.table("departamentos").delete().eq("tenant_id", tenant_id).eq("id", did).execute()
                                st.success("Departamento excluído.")
                                safe_rerun()
                            except Exception as e:
                                st.error(f"Erro ao excluir: {e}")

                admin_divider()

                # ------------------------------
                # Grupos do departamento (com busca + paginação)
                # ------------------------------
                st.markdown("#### Grupos deste departamento")
                g1, g2, g3, g4 = st.columns([0.42, 0.18, 0.20, 0.20], gap="small")
                with g1:
                    g_search = st.text_input("Buscar grupos", placeholder="Buscar grupo…", key=f"dep_{did}__g_search")
                with g2:
                    g_only_active = st.toggle("Só ativos", value=True, key=f"dep_{did}__g_only_active")
                with g3:
                    g_sort = st.selectbox(
                        "Ordenar",
                        ["A–Z", "Z–A", "Ativos primeiro", "Inativos primeiro"],
                        index=0,
                        key=f"dep_{did}__g_sort",
                    )
                with g4:
                    g_page_size = st.selectbox("Por página", [5, 10, 20], index=1, key=f"dep_{did}__g_page_size")

                qg = (
                    sb.table("equip_grupos")
                    .select("id,nome,ativo,departamento_id")
                    .eq("tenant_id", tenant_id)
                    .eq("departamento_id", did)
                    .order("nome")
                )
                if g_only_active:
                    qg = qg.eq("ativo", True)
                if g_search:
                    try:
                        qg = qg.ilike("nome", f"%{g_search.strip()}%")
                    except Exception:
                        pass

                grupos = qg.execute().data or []
                if g_search:
                    ss2 = g_search.strip().lower()
                    grupos = [x for x in grupos if ss2 in str(x.get("nome", "")).lower()]

                # Ordenação local (A–Z, etc.)
                try:
                    gsm = g_sort
                except Exception:
                    gsm = "A–Z"
                if gsm == "A–Z":
                    grupos = sorted(grupos, key=lambda x: str(x.get("nome", "")).lower())
                elif gsm == "Z–A":
                    grupos = sorted(grupos, key=lambda x: str(x.get("nome", "")).lower(), reverse=True)
                elif gsm == "Ativos primeiro":
                    grupos = sorted(grupos, key=lambda x: (0 if x.get("ativo") else 1, str(x.get("nome", "")).lower()))
                elif gsm == "Inativos primeiro":
                    grupos = sorted(grupos, key=lambda x: (0 if not x.get("ativo") else 1, str(x.get("nome", "")).lower()))

                if not grupos:
                    st.info("Nenhum grupo encontrado para este departamento.")
                else:
                    g_page_idx, _ = pager(f"dep_{did}__grps", total=len(grupos), page_size=int(g_page_size))
                    gs = g_page_idx * int(g_page_size)
                    ge = gs + int(g_page_size)
                    grupos_page = grupos[gs:ge]

                    # Conta equipamentos por grupo (best-effort) — só para os itens da página
                    equip_counts: dict[str, int] = {}
                    for gg in grupos_page:
                        try:
                            cnt = (
                                sb.table("equipamentos")
                                .select("id", count="exact")
                                .eq("tenant_id", tenant_id)
                                .eq("grupo_id", gg["id"])
                                .execute()
                                .count
                            )
                            equip_counts[gg["id"]] = int(cnt or 0)
                        except Exception:
                            equip_counts[gg["id"]] = 0

                    # Lista em cards enterprise
                    for gg in grupos_page:
                        g_status_txt = "ATIVO" if gg.get("ativo") else "INATIVO"
                        g_status_cls = "badge-active" if gg.get("ativo") else "badge-inactive"
                        st.markdown(
                            f"""
    <div class='card-enterprise'>
      <div style='display:flex; align-items:flex-start; justify-content:space-between; gap:10px;'>
        <div>
          <div style='font-weight:800; font-size:14px; margin-bottom:6px;'>{gg['nome']}</div>
          <div class='small-muted'>Grupo ID: {gg['id']}</div>
        </div>
        <div style='text-align:right; white-space:nowrap;'>
          <span class='badge {g_status_cls}'>{g_status_txt}</span>
          <span class='badge badge-neutral'>{equip_counts.get(gg['id'], 0)} equip.</span>
        </div>
      </div>
    </div>
    """,
                            unsafe_allow_html=True,
                        )

