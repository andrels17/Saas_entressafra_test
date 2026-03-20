from src.ui.core.styles import page_header as _ph
import streamlit as st
from src.ui.admin_components.layout import admin_block, admin_divider
from src.ui.admin_components.utils import inject_enterprise_css, pager, safe_rerun, norm_name
from src.utils.supabase_helpers import sb_for_user, current_tenant_id, current_role
from src.db.supabase_client import get_supabase_anon
from src.auth.audit import (
    audit_grupo_criado,
    audit_grupo_atualizado,
    audit_grupo_deletado,
    audit_grupo_toggle,
)


# ── Funções de leitura cacheadas ─────────────────────────────────────────────

@st.cache_data(ttl=30, show_spinner=False)
def _load_grupos_admin(tenant_id: str, with_dept: bool = True,
                       _token: str = "") -> list[dict]:
    """Carrega grupos para a tela de admin (cacheado, ttl=30s)."""
    sb = get_supabase_anon()
    if _token:
        sb.postgrest.auth(_token)
    try:
        sel = "id,nome,ativo,created_at,departamento_id" if with_dept else "id,nome,ativo,created_at"
        return (
            sb.table("equip_grupos")
            .select(sel)
            .eq("tenant_id", tenant_id)
            .order("nome")
            .execute()
            .data
        ) or []
    except Exception:
        return []


@st.cache_data(ttl=30, show_spinner=False)
def _load_departamentos_admin(tenant_id: str, _token: str = "") -> list[dict]:
    """Carrega departamentos ativos para o admin (cacheado, ttl=30s)."""
    sb = get_supabase_anon()
    if _token:
        sb.postgrest.auth(_token)
    try:
        return (
            sb.table("departamentos")
            .select("id,nome,ativo")
            .eq("tenant_id", tenant_id)
            .eq("ativo", True)
            .order("nome")
            .execute()
            .data
        ) or []
    except Exception:
        return []


@st.cache_data(ttl=30, show_spinner=False)
def _load_equipamentos_count_por_grupo(tenant_id: str,
                                        _token: str = "") -> dict[str, int]:
    """Retorna dict grupo_id → contagem de equipamentos (cacheado)."""
    sb = get_supabase_anon()
    if _token:
        sb.postgrest.auth(_token)
    try:
        rows = (
            sb.table("equipamentos")
            .select("id,grupo_id")
            .eq("tenant_id", tenant_id)
            .execute()
            .data
        ) or []
        counts: dict[str, int] = {}
        for r in rows:
            gid = r.get("grupo_id")
            if gid:
                counts[gid] = counts.get(gid, 0) + 1
        return counts
    except Exception:
        return {}


# _inject_enterprise_css → moved to admin_components.utils


# clamp → clamp from admin_components.utils


# pager → pager from admin_components.utils


# safe_rerun → safe_rerun from admin_components.utils


# norm_name → norm_name from admin_components.utils


def _render_limpeza_grupos(sb, tenant_id: str, deps: list[dict]):
    with st.expander("🧹 Limpeza de Grupos (anti-duplicidade)", expanded=False):
        st.caption(
            "Deduplicar grupos (mesmo nome) e remover grupos vazios (sem equipamentos). "
            "Na deduplicação, os equipamentos e vínculos de serviços são reapontados para o grupo canônico.")

        # Carrega grupos
        try:
            grupos = (
                sb.table("equip_grupos")
                .select("id,nome,ativo,created_at,departamento_id")
                .eq("tenant_id", tenant_id)
                .execute()
                .data
            ) or []
        except Exception:
            grupos = (
                sb.table("equip_grupos")
                .select("id,nome,ativo,created_at")
                .eq("tenant_id", tenant_id)
                .execute()
                .data
            ) or []

        # Conta equipamentos por grupo (best-effort)
        equip_counts: dict[str, int] = {}
        try:
            eq = (
                sb.table("equipamentos")
                .select("id,grupo_id")
                .eq("tenant_id", tenant_id)
                .execute()
                .data
            ) or []
            for r in eq:
                gid = r.get("grupo_id")
                if gid:
                    equip_counts[gid] = equip_counts.get(gid, 0) + 1
        except Exception:
            equip_counts = {}

        # Opções de chave
        consider_depto = st.toggle(
            "Considerar Departamento ao deduplicar",
            value=True,
            key="grp_dedupe_consider_depto")

        buckets: dict[str, list[dict]] = {}
        for g in grupos:
            nm = norm_name(str(g.get("nome", "")))
            if not nm:
                continue
            dep_id = g.get("departamento_id") if consider_depto else None
            key = f"{dep_id or 'NULL'}::{nm}"
            buckets.setdefault(key, []).append(g)
        dup_buckets = {k: v for k, v in buckets.items() if len(v) > 1}

        c1, c2, c3 = st.columns(3, gap="small")
        c1.metric("Grupos", len(grupos))
        c2.metric("Duplicados", sum(len(v) for v in dup_buckets.values()))
        c3.metric(
            "Sem equipamentos", sum(
                1 for g in grupos if equip_counts.get(
                    g.get("id"), 0) == 0))

        if dup_buckets:
            st.markdown("**Amostra de duplicidades:**")
            dep_name_by_id = {d.get("id"): d.get("nome")
                              for d in (deps or []) if d.get("id")}
            sample_rows = []
            for k, v in list(dup_buckets.items())[:10]:
                any_dep = v[0].get("departamento_id")
                sample_rows.append(
                    {
                        "departamento": dep_name_by_id.get(any_dep, "(sem)"),
                        "itens": len(v),
                        "nomes": ", ".join([str(x.get("nome")) for x in v][:6]),
                    }
                )
            st.dataframe(
                sample_rows,
                use_container_width=True,
                hide_index=True)
        else:
            st.info("Nenhuma duplicidade óbvia encontrada.")

        admin_divider()

        st.markdown("#### Deduplicar")
        st.caption(
            "Mantém 1 grupo (preferindo **ativo** e com **mais equipamentos**), reaponta: "
            "- equipamentos.grupo_id\n- grupo_servicos.grupo_id (se existir)\nDepois tenta apagar duplicados; se falhar, desativa.")
        confirm_txt = st.text_input(
            "Digite DEDUPLICAR",
            value="",
            key="grp_dedupe_confirm")
        if st.button(
            "Deduplicar agora",
            type="primary",
            use_container_width=True,
            disabled=(confirm_txt.strip().upper() != "DEDUPLICAR"),
            key="grp_dedupe_btn",
        ):
            changed = 0
            for _, items in dup_buckets.items():
                def score(g):
                    gid = g.get("id")
                    return (
                        1 if g.get("ativo") else 0, equip_counts.get(
                            gid, 0), "9999" if g.get("created_at") is None else str(
                            g.get("created_at")), )

                items_sorted = sorted(items, key=score, reverse=True)
                canon = items_sorted[0]
                canon_id = canon.get("id")
                for g in items_sorted[1:]:
                    gid = g.get("id")
                    if not gid or gid == canon_id:
                        continue

                    # 1) Reaponta equipamentos
                    try:
                        sb.table("equipamentos").update({"grupo_id": canon_id}).eq(
                            "tenant_id", tenant_id).eq("grupo_id", gid).execute()
                    except Exception as e:
                        st.error(
                            f"Falha ao atualizar equipamentos.grupo_id ({gid} -> {canon_id}): {e}")
                        continue

                    # 2) Reaponta grupo_servicos (se existir)
                    try:
                        sb.table("grupo_servicos").update({"grupo_id": canon_id}).eq(
                            "tenant_id", tenant_id).eq("grupo_id", gid).execute()
                    except Exception as _e:
                        st.warning(f"Erro ao salvar: {_e}")

                    # 3) Apaga ou desativa duplicado
                    try:
                        sb.table("equip_grupos").delete().eq(
                            "tenant_id", tenant_id).eq(
                            "id", gid).execute()
                    except Exception:
                        try:
                            sb.table("equip_grupos").update({"ativo": False}).eq(
                                "tenant_id", tenant_id).eq("id", gid).execute()
                        except Exception as e:
                            st.error(
                                f"Falha ao desativar grupo duplicado ({gid}): {e}")
                            continue
                    changed += 1

            st.success(
                f"Deduplicação concluída. Itens processados: {changed}.")
            safe_rerun()

        admin_divider()

        st.markdown("#### Remover grupos vazios")
        st.caption(
            "Remove grupos sem equipamentos. Você pode **desativar** ou **apagar**.")
        mode = st.radio("Ação", ["Desativar", "Apagar"],
                        horizontal=True, key="grp_empty_mode")
        confirm2 = st.text_input(
            "Digite LIMPAR",
            value="",
            key="grp_empty_confirm")
        if st.button(
            "Limpar vazios",
            use_container_width=True,
            disabled=(confirm2.strip().upper() != "LIMPAR"),
            key="grp_empty_btn",
        ):
            empty_ids = [
                g.get("id") for g in grupos if equip_counts.get(
                    g.get("id"), 0) == 0 and g.get("id")]
            if not empty_ids:
                st.info("Nenhum grupo vazio encontrado.")
            else:
                ok = 0
                for gid in empty_ids:
                    try:
                        if mode == "Apagar":
                            # remove vínculos de serviços antes (best-effort)
                            try:
                                sb.table("grupo_servicos").delete().eq(
                                    "tenant_id", tenant_id).eq(
                                    "grupo_id", gid).execute()
                            except Exception as _e:
                                import logging; logging.getLogger("saas").warning("grupos.py: %s", _e)
                            sb.table("equip_grupos").delete().eq(
                                "tenant_id", tenant_id).eq("id", gid).execute()
                        else:
                            sb.table("equip_grupos").update({"ativo": False}).eq(
                                "tenant_id", tenant_id).eq("id", gid).execute()
                        ok += 1
                    except Exception as e:
                        st.error(f"Falha ao limpar grupo {gid}: {e}")
                st.success(f"Limpeza concluída. Itens afetados: {ok}.")
                safe_rerun()


def _render_limpeza_total_grupos(sb, tenant_id: str):
    """Limpeza 'de verdade' para grupos: desativar/apagar em massa.

    Para hard delete, primeiro desvincula equipamentos.grupo_id e (se existirem) tabelas relacionadas.
    """
    with st.expander("🧼 Limpeza completa de Grupos", expanded=False):
        st.caption(
            "Use isso para **limpar de verdade** (em massa) a tabela de grupos. "
            "Para evitar erros, o sistema tenta desvincular equipamentos e vínculos conhecidos antes de apagar.")

        try:
            grupos = (
                sb.table("equip_grupos")
                .select("id,nome,ativo,created_at,departamento_id")
                .eq("tenant_id", tenant_id)
                .execute()
                .data
            ) or []
        except Exception:
            grupos = (
                sb.table("equip_grupos")
                .select("id,nome,ativo,created_at")
                .eq("tenant_id", tenant_id)
                .execute()
                .data
            ) or []

        c1, c2, c3 = st.columns(3, gap="small")
        c1.metric("Grupos", len(grupos))
        c2.metric("Ativos", sum(1 for g in grupos if g.get("ativo")))
        c3.metric("Inativos", sum(1 for g in grupos if not g.get("ativo")))

        st.markdown("#### Backup rápido")
        if grupos:
            try:
                import pandas as _pd
                df = _pd.DataFrame(grupos)
                csv = df.to_csv(index=False).encode("utf-8")
            except Exception:
                import csv as _csv
                import io as _io
                buf = _io.StringIO()
                w = _csv.DictWriter(buf, fieldnames=sorted(
                    {k for r in grupos for k in r.keys()}))
                w.writeheader()
                for r in grupos:
                    w.writerow(r)
                csv = buf.getvalue().encode('utf-8')

            st.download_button(
                "Baixar CSV de grupos", icon=":material/download:",
                data=csv,
                file_name="grupos_backup.csv",
                mime="text/csv",
                use_container_width=True,
                key="grp_backup_csv_btn",
            )
        else:
            st.info("Não há grupos para exportar.")

        admin_divider()

        st.markdown("#### Limpar em massa")
        alvo = st.radio("O que limpar",
                        ["Somente inativos",
                         "Todos (inclusive ativos)"],
                        horizontal=True,
                        key="grp_mass_target")
        acao = st.radio("Como limpar",
                        ["Desativar (soft)",
                         "Apagar definitivamente (hard)"],
                        horizontal=True,
                        key="grp_mass_mode")

        st.warning(
            "⚠️ **Apagar definitivamente** pode falhar se existirem vínculos protegidos por Foreign Key. "
            "Antes de apagar, tentamos desvincular equipamentos e tabelas relacionadas (best-effort).")

        confirm = st.text_input(
            "Digite LIMPAR GRUPOS",
            value="",
            key="grp_mass_confirm")
        confirm_ck = st.checkbox(
            "Estou ciente e quero executar",
            value=False,
            key="grp_mass_ack")

        if st.button(
            "Executar limpeza",
            type="primary",
            use_container_width=True,
            disabled=(
                confirm.strip().upper() != "LIMPAR GRUPOS" or not confirm_ck),
            key="grp_mass_btn",
        ):
            ids = []
            for g in grupos:
                if alvo.startswith("Somente") and g.get("ativo"):
                    continue
                if g.get("id"):
                    ids.append(g["id"])

            if not ids:
                st.info("Nada para limpar com os critérios selecionados.")
                return

            ok = 0
            for gid in ids:
                try:
                    if acao.startswith("Desativar"):
                        sb.table("equip_grupos").update({"ativo": False}).eq(
                            "tenant_id", tenant_id).eq("id", gid).execute()
                        ok += 1
                        continue

                    # HARD DELETE: desvincula e apaga
                    try:
                        sb.table("equipamentos").update({"grupo_id": None}).eq(
                            "tenant_id", tenant_id).eq("grupo_id", gid).execute()
                    except Exception as e:
                        st.error(
                            f"Falha ao desvincular equipamentos do grupo {gid}: {e}")
                        raise

                    # Tabelas relacionadas (best-effort)
                    for tbl, col in [
                        ("grupo_servicos", "grupo_id"),
                        ("equip_grupo_servicos", "grupo_id"),
                        ("grupos_servicos", "grupo_id"),
                    ]:
                        try:
                            sb.table(tbl).delete().eq(
                                "tenant_id", tenant_id).eq(
                                col, gid).execute()
                        except Exception:
                            # tenta pelo menos setar NULL caso delete não seja
                            # permitido
                            try:
                                sb.table(tbl).update({col: None}).eq(
                                    "tenant_id", tenant_id).eq(col, gid).execute()
                            except Exception as _e:
                                import logging; logging.getLogger("saas").warning("grupos.py: %s", _e)

                    sb.table("equip_grupos").delete().eq(
                        "tenant_id", tenant_id).eq(
                        "id", gid).execute()
                    ok += 1
                except Exception as e:
                    st.error(f"Erro ao limpar grupo {gid}: {e}")
                    break

            st.success(f"Limpeza concluída. Itens afetados: {ok}.")
            safe_rerun()


def render_admin_grupos() -> None:
    _ph("⊕", "Grupos de Equipamentos",
        "Crie e gerencie grupos. Mova equipamentos em lote para cada grupo.")
    inject_enterprise_css()

    role = current_role()
    if role not in ("admin", "superadmin"):
        st.error("Apenas Admin pode gerenciar grupos.")
        st.stop()

    tenant_id = current_tenant_id()
    sb = sb_for_user()
    token = st.session_state.get("sb_access_token", "")

    # Departamentos (cacheado — evita query a cada rerun)
    deps = _load_departamentos_admin(tenant_id, token)
    dep_map = {d["nome"]: d["id"] for d in deps}
    dep_names = ["(sem departamento)"] + [d["nome"] for d in deps]

    tab_manage, tab_clean = st.tabs(["📋 Gerenciar", "🧹 Limpeza"])

    with tab_clean:
        # Limpeza (anti-duplicidade / vazios)
        _render_limpeza_grupos(sb, tenant_id, deps)

        # Limpeza completa (massa)
        _render_limpeza_total_grupos(sb, tenant_id)

    with tab_manage:
        # Criar grupo
        with st.form("create_group"):
            nome = st.text_input("Novo grupo",
                                 placeholder="Ex.: Tratores Transbordos")
            dep_sel = None
            if deps:
                dep_sel = st.selectbox(
                    "Departamento (opcional)", dep_names, index=0)
            submitted = st.form_submit_button(
                "Criar grupo", use_container_width=True)

        if submitted:
            nome_norm = (nome or "").strip()
            if not nome_norm:
                st.warning("Informe um nome.")
                st.stop()
            try:
                payload = {"tenant_id": tenant_id, "nome": nome_norm}
                if dep_sel and dep_sel != "(sem departamento)":
                    payload["departamento_id"] = dep_map.get(dep_sel)
                try:
                    res = sb.table("equip_grupos").insert(payload).execute()
                except Exception:
                    # Fallback: se a coluna departamento_id não existir ainda
                    payload.pop("departamento_id", None)
                    res = sb.table("equip_grupos").insert(payload).execute()
                new_id = (res.data or [{}])[0].get("id", "")
                audit_grupo_criado(new_id, nome_norm, payload.get("departamento_id"))
                st.success("Grupo criado.")
                safe_rerun()
            except Exception as e:
                st.error(f"Erro ao criar grupo: {e}")

        admin_divider()

        # ------------------------------
        # Listar grupos (com busca + paginação)
        # ------------------------------
        admin_block(
            "Lista de grupos",
            "Gerencie busca, status e paginação dos grupos.")

        f1, f2, f3, f4 = st.columns([0.46, 0.18, 0.18, 0.18], gap="small")
        with f1:
            grp_search = st.text_input(
                "Buscar",
                placeholder="Buscar por nome…",
                key="grp_search")
        with f2:
            only_active = st.toggle(
                "Só ativos", value=True, key="grp_only_active")
        with f3:
            sort_mode = st.selectbox("Ordenar",
                                     ["A–Z",
                                      "Z–A",
                                      "Ativos primeiro",
                                      "Inativos primeiro",
                                      "Mais recentes",
                                      "Mais antigos"],
                                     index=0,
                                     key="grp_sort_mode",
                                     )
        with f4:
            page_size = st.selectbox(
                "Por página", [
                    10, 20, 50], index=0, key="grp_page_size")

        # Leitura cacheada — evita re-query a cada rerun por filtro/paginação
        grupos = _load_grupos_admin(tenant_id, with_dept=bool(deps), _token=token)
        if only_active:
            grupos = [g for g in grupos if g.get("ativo")]

        if grp_search:
            ss = grp_search.strip().lower()
            grupos = [g for g in grupos if ss in str(g.get("nome", "")).lower()]

        # Ordenação local
        try:
            sm = sort_mode
        except Exception:
            sm = "A–Z"
        if sm == "A–Z":
            grupos = sorted(grupos, key=lambda x: str(x.get("nome", "")).lower())
        elif sm == "Z–A":
            grupos = sorted(grupos, key=lambda x: str(x.get("nome", "")).lower(), reverse=True)
        elif sm == "Ativos primeiro":
            grupos = sorted(grupos, key=lambda x: (0 if x.get("ativo") else 1, str(x.get("nome", "")).lower()))
        elif sm == "Inativos primeiro":
            grupos = sorted(grupos, key=lambda x: (0 if not x.get("ativo") else 1, str(x.get("nome", "")).lower()))
        elif sm == "Mais recentes":
            grupos = sorted(grupos, key=lambda x: str(x.get("created_at", "")), reverse=True)
        elif sm == "Mais antigos":
            grupos = sorted(grupos, key=lambda x: str(x.get("created_at", "")))

        if not grupos:
            st.info("Nenhum grupo encontrado.")
            return

        # Contagem de equipamentos por grupo — 1 query cacheada para todos os grupos
        # (era 1 query por grupo dentro do for loop = N+1)
        eq_count_por_grupo = _load_equipamentos_count_por_grupo(tenant_id, token)
        total_equip = sum(eq_count_por_grupo.values())

        k1, k2, k3 = st.columns(3, gap="small")
        k1.metric("Grupos", len(grupos))
        k2.metric("Equipamentos", total_equip)
        k3.metric("Departamentos", len(deps) if deps else 0)

        st.caption(f"Total: **{len(grupos)}**")
        page_idx, _ = pager("grps", total=len(
            grupos), page_size=int(page_size))
        start = page_idx * int(page_size)
        end = start + int(page_size)
        grupos_page = grupos[start:end]

        for g in grupos_page:
            gid = g["id"]
            # badge de equipamentos — lookup no dict cacheado (sem nova query)
            eq_cnt = eq_count_por_grupo.get(gid, 0)

            dep_nome = "(sem)"
            if deps:
                dep_nome = next(
                    (d["nome"] for d in deps if d["id"] == g.get("departamento_id")), "(sem)")

            with st.expander(f"{g['nome']}", expanded=False):
                status_txt = "ATIVO" if g.get("ativo") else "INATIVO"
                status_cls = "badge-active" if g.get(
                    "ativo") else "badge-inactive"
                st.markdown(
                    f"""
    <div class='card-enterprise'>
      <div style='display:flex; align-items:flex-start; justify-content:space-between; gap:10px;'>
        <div>
          <div style='font-size:16px; font-weight:800; margin-bottom:6px;'>{g['nome']}</div>
          <div class='small-muted'>Departamento: {dep_nome}</div>
          <div class='small-muted'>Grupo ID: {gid}</div>
        </div>
        <div style='text-align:right; white-space:nowrap;'>
          <span class='badge {status_cls}'>{status_txt}</span>
          <span class='badge badge-neutral'>{eq_cnt} equipamentos</span>
        </div>
      </div>
    </div>
    """,
                    unsafe_allow_html=True,
                )

                # ------------------------------
                # Edição do grupo
                # ------------------------------
                c1, c2, c3 = st.columns([0.42, 0.25, 0.33], gap="small")
                with c1:
                    st.caption(f"Ativo: {'Sim' if g.get('ativo') else 'Não'}")
                with c2:
                    novo_nome = st.text_input(
                        "Renomear", value=g["nome"], key=f"rename_{gid}")
                with c3:
                    dep_new = None
                    if deps:
                        current_dep_name = next(
                            (d["nome"] for d in deps if d["id"] == g.get("departamento_id")),
                            "(sem departamento)")
                        idx = dep_names.index(
                            current_dep_name) if current_dep_name in dep_names else 0
                        dep_new = st.selectbox(
                            "Departamento", dep_names, index=idx, key=f"dep_sel_{gid}")
                    else:
                        st.caption("Departamentos: rode a etapa9")

                a1, a2, a3 = st.columns([1, 1, 1], gap="small")
                with a1:
                    if st.button(
                        "Salvar",
                        icon=":material/save:",
                        key=f"save_{gid}",
                            use_container_width=True):
                        nn = (novo_nome or "").strip()
                        if not nn:
                            st.warning("Nome inválido.")
                            st.stop()
                        try:
                            payload = {"nome": nn}
                            if deps and dep_new is not None:
                                payload["departamento_id"] = None if dep_new == "(sem departamento)" else dep_map.get(
                                    dep_new)
                            try:
                                sb.table("equip_grupos").update(payload).eq(
                                    "tenant_id", tenant_id).eq("id", gid).execute()
                            except Exception:
                                payload.pop("departamento_id", None)
                                sb.table("equip_grupos").update(payload).eq(
                                    "tenant_id", tenant_id).eq("id", gid).execute()
                            audit_grupo_atualizado(gid, g.get("nome", ""), payload)
                            st.toast(
                                "✓ Atualizado", icon=":material/check_circle:")
                            safe_rerun()
                        except Exception as e:
                            st.error(f"Erro ao salvar: {e}")

                with a2:
                    label = "Desativar" if g.get("ativo") else "Ativar"
                    if st.button(
                            label,
                            key=f"toggle_{gid}",
                            use_container_width=True):
                        try:
                            novo_ativo = not g.get("ativo")
                            sb.table("equip_grupos").update({"ativo": novo_ativo}).eq(
                                "tenant_id", tenant_id).eq("id", gid).execute()
                            audit_grupo_toggle(gid, g.get("nome", ""), novo_ativo)
                            st.toast("✓ Ok", icon=":material/check_circle:")
                            safe_rerun()
                        except Exception as e:
                            st.error(f"Erro: {e}")

                with a3:
                    with st.popover("Excluir", icon=":material/delete:", help="Remover grupo permanentemente"):
                        st.caption(
                            "Isso remove o grupo. Equipamentos do grupo serão **desvinculados** (grupo_id = NULL).")
                        st.caption(f"Equipamentos vinculados: **{eq_cnt}**")
                        confirm = st.checkbox(
                            "Confirmo excluir", value=False, key=f"grp_del_ok_{gid}")
                        if st.button(
                            "Apagar agora",
                            type="primary",
                            use_container_width=True,
                            disabled=not confirm,
                                key=f"grp_del_{gid}"):
                            try:
                                sb.table("equipamentos").update({"grupo_id": None}).eq(
                                    "tenant_id", tenant_id).eq("grupo_id", gid).execute()
                            except Exception as _e:
                                import logging; logging.getLogger("saas").warning("grupos.py: %s", _e)
                            try:
                                sb.table("equip_grupos").delete().eq(
                                    "tenant_id", tenant_id).eq("id", gid).execute()
                                audit_grupo_deletado(gid, g.get("nome", ""),
                                                     equipamentos_desvinculados=eq_cnt)
                                st.success("Grupo excluído.")
                                safe_rerun()
                            except Exception as e:
                                st.error(f"Erro ao excluir: {e}")

                admin_divider()

                # ------------------------------
                # Equipamentos do grupo (busca + paginação)
                # ------------------------------
                st.markdown("#### Equipamentos deste grupo")
                e1, e2, e3, e4 = st.columns(
                    [0.42, 0.18, 0.20, 0.20], gap="small")
                with e1:
                    eq_search = st.text_input(
                        "Buscar equipamentos",
                        placeholder="Buscar por frota/modelo…",
                        key=f"grp_{gid}__eq_search")
                with e2:
                    eq_only_active = st.toggle(
                        "Só ativos", value=True, key=f"grp_{gid}__eq_only_active")
                with e3:
                    eq_sort = st.selectbox(
                        "Ordenar",
                        ["Frota A–Z", "Frota Z–A", "Mais recentes", "Mais antigos"],
                        index=0,
                        key=f"grp_{gid}__eq_sort",
                    )
                with e4:
                    eq_page_size = st.selectbox(
                        "Por página", [
                            10, 20, 50], index=0, key=f"grp_{gid}__eq_page_size")

                # Seleção com fallback (caso algumas colunas não existam)
                select_cols = "id,frota,modelo,status,ativo,grupo_id"
                try:
                    qe = sb.table("equipamentos").select(
                        select_cols,
                        count="exact").eq(
                        "tenant_id",
                        tenant_id).eq(
                        "grupo_id",
                        gid)
                except Exception:
                    select_cols = "id,frota,ativo,grupo_id"
                    qe = sb.table("equipamentos").select(
                        select_cols,
                        count="exact").eq(
                        "tenant_id",
                        tenant_id).eq(
                        "grupo_id",
                        gid)

                if eq_only_active:
                    try:
                        qe = qe.eq("ativo", True)
                    except Exception as _e:
                        st.warning(f"Erro ao salvar: {_e}")

                if eq_search:
                    # ilike em frota e modelo (best-effort)
                    term = eq_search.strip()
                    try:
                        # PostgREST: or=(frota.ilike.*x*,modelo.ilike.*x*)
                        qe = qe.or_(
                            f"frota.ilike.%{term}%,modelo.ilike.%{term}%")
                    except Exception as _e:
                        st.warning(f"Erro ao salvar: {_e}")

                # Ordenação (best-effort)
                try:
                    if eq_sort == "Frota A–Z":
                        qe = qe.order("frota")
                    elif eq_sort == "Frota Z–A":
                        qe = qe.order("frota", desc=True)
                    elif eq_sort == "Mais recentes":
                        # tenta created_at, senão id
                        try:
                            qe = qe.order("created_at", desc=True)
                        except Exception:
                            qe = qe.order("id", desc=True)
                    elif eq_sort == "Mais antigos":
                        try:
                            qe = qe.order("created_at")
                        except Exception:
                            qe = qe.order("id")
                except Exception as _e:
                    import logging; logging.getLogger("saas").warning("grupos.py: %s", _e)

                # paginação via range
                try:
                    total_eq = int(getattr(qe.execute(), "count", 0) or 0)
                except Exception:
                    total_eq = 0

                if total_eq <= 0:
                    st.info("Nenhum equipamento encontrado.")
                else:
                    eq_page_idx, _ = pager(
                        f"grp_{gid}__eq", total=total_eq, page_size=int(eq_page_size))
                    rs = eq_page_idx * int(eq_page_size)
                    re = rs + int(eq_page_size) - 1
                    try:
                        res = qe.range(rs, re).execute()
                        rows = res.data or []
                    except Exception:
                        rows = []

                    if not rows:
                        st.info("Nenhum equipamento nesta página.")
                    else:
                        # Lista em cards enterprise (mais legível que tabela
                        # longa)
                        for r in rows:
                            frota = r.get("frota") or "-"
                            modelo = r.get("modelo") or ""
                            status = r.get("status") or ""
                            ativo = r.get("ativo", True)
                            b_cls = "badge-active" if ativo else "badge-inactive"
                            b_txt = "ATIVO" if ativo else "INATIVO"

                            st.markdown(
                                f"""
    <div class='card-enterprise'>
      <div style='display:flex; align-items:flex-start; justify-content:space-between; gap:10px;'>
        <div>
          <div style='font-weight:800; font-size:14px; margin-bottom:6px;'>{frota}{(' · ' + modelo) if modelo else ''}</div>
          <div class='small-muted'>{('Status: ' + status) if status else ''}</div>
          <div class='small-muted'>Equipamento ID: {r.get('id', '')}</div>
        </div>
        <div style='text-align:right; white-space:nowrap;'>
          <span class='badge {b_cls}'>{b_txt}</span>
        </div>
      </div>
    </div>
    """,
                                unsafe_allow_html=True,
                            )

            st.markdown(
                "<div style='height:6px'></div>",
                unsafe_allow_html=True)
