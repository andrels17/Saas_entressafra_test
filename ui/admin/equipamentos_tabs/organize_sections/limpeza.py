
import streamlit as st
import pandas as pd
from postgrest.exceptions import APIError

from src.ui.admin_components.layout import admin_block, admin_divider
from src.ui.admin.equipamentos_helpers import (
    _rerun,
    _load_grupos,
    _load_departamentos,
    _load_user_names,
    _audit,
    _chunked,
    _safe_int,
)


def render_limpeza_section(sb, tenant_id: str):
            st.markdown("### Limpeza da tabela de equipamentos")
            st.caption(
                "Ações de manutenção para **excluir em massa**. "
                "Apagar definitivamente pode ser **irreversível** e pode falhar se houver vínculos (ex.: pedidos/histórico)."
            )

            # Contadores rápidos
            try:
                active_count = (
                    sb.table("equipamentos")
                    .select("id", count="exact")
                    .eq("tenant_id", tenant_id)
                    .eq("ativo", True)
                    .execute()
                    .count
                ) or 0
            except Exception:
                active_count = None

            try:
                trash_count = (
                    sb.table("equipamentos")
                    .select("id", count="exact")
                    .eq("tenant_id", tenant_id)
                    .eq("ativo", False)
                    .execute()
                    .count
                ) or 0
            except Exception:
                trash_count = None

            c1, c2, c3 = st.columns(3)
            c1.metric("Ativos", "—" if active_count is None else active_count)
            c2.metric("Na lixeira", "—" if trash_count is None else trash_count)
            if (active_count is not None) and (trash_count is not None):
                c3.metric("Total", active_count + trash_count)
            else:
                c3.metric("Total", "—")

            admin_divider()
            st.markdown("#### Backup rápido")
            st.caption("Antes de limpar, você pode baixar um CSV com os equipamentos do seu tenant.")

            try:
                all_rows = (
                    sb.table("equipamentos")
                    .select("id, frota, modelo, ano, status, grupo_id, departamento_id, ativo")
                    .eq("tenant_id", tenant_id)
                    .order("frota")
                    .execute()
                    .data
                ) or []
            except Exception:
                all_rows = []

            if all_rows:
                df_bk = pd.DataFrame(all_rows)
                csv_data = df_bk.to_csv(index=False).encode("utf-8")
                st.download_button(
                    "Baixar backup CSV (equipamentos)", icon=":material/download:",
                    data=csv_data,
                    file_name="backup_equipamentos.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
            else:
                st.info("Não foi possível gerar o backup (ou não há equipamentos).")

            admin_divider()
            st.markdown("#### Opções de limpeza")

            colA, colB = st.columns(2)
            with colA:
                st.markdown("**1) Enviar tudo para a lixeira (soft delete)**")
                st.caption("Marca `ativo = false` para todos os equipamentos do tenant. Reversível pela aba **Lixeira**.")
                confirm_soft = st.checkbox(
                    "Confirmo que quero desativar TODOS os equipamentos",
                    key="eq_clean_soft_confirm",
                )
                if st.button(
                    "Desativar todos (lixeira)", icon=":material/block:",
                    type="primary",
                    use_container_width=True,
                    disabled=not confirm_soft,
                    key="eq_clean_soft_btn",
                ):
                    try:
                        sb.table("equipamentos").update({"ativo": False}).eq("tenant_id", tenant_id).execute()
                        _audit(sb, tenant_id, "soft_delete", {"scope": "all"})
                        st.success("Todos os equipamentos foram enviados para a lixeira.")
                        _rerun()
                    except Exception as e:
                        st.error(f"Erro ao desativar todos: {e}")

            with colB:
                st.markdown("**2) Limpar definitivamente (hard delete)**")
                st.caption(
                    "Apaga registros da tabela `equipamentos` do tenant. "
                    "Se houver chaves estrangeiras (ex.: pedidos/histórico), o banco pode bloquear a operação."
                )

                clean_mode = st.radio(
                    "O que apagar?",
                    options=["Somente lixeira (ativo=false)", "Tudo (ativos + lixeira)"],
                    index=0,
                    key="eq_clean_mode",
                )
                phrase = st.text_input(
                    "Para confirmar, digite **LIMPAR**",
                    value="",
                    key="eq_clean_phrase",
                    help="Evita cliques acidentais.",
                )
                confirm_hard = st.checkbox(
                    "Entendo que esta ação pode ser irreversível",
                    key="eq_clean_hard_confirm",
                )

                disabled = (phrase.strip().upper() != "LIMPAR") or (not confirm_hard)
                if st.button(
                    "Apagar definitivamente",
                    use_container_width=True,
                    disabled=disabled,
                    key="eq_clean_hard_btn",
                ):
                    try:
                        if clean_mode.startswith("Somente"):
                            sb.table("equipamentos").delete().eq("tenant_id", tenant_id).eq("ativo", False).execute()
                            _audit(sb, tenant_id, "hard_delete", {"scope": "trash"})
                            st.success("Lixeira apagada definitivamente.")
                        else:
                            # Tenta primeiro apagar lixeira, depois ativos.
                            try:
                                sb.table("equipamentos").delete().eq("tenant_id", tenant_id).eq("ativo", False).execute()
                            except Exception:
                                pass
                            sb.table("equipamentos").delete().eq("tenant_id", tenant_id).execute()
                            _audit(sb, tenant_id, "hard_delete", {"scope": "all"})
                            st.success("Tabela de equipamentos limpa (tenant).")
                        _rerun()
                    except APIError as e:
                        try:
                            st.json(e.json())
                        except Exception:
                            st.error(str(e))
                        st.warning(
                            "Se aparecer erro de **foreign key**/constraint, o banco está impedindo o delete porque existem registros relacionados. "
                            "Nesse caso, use **soft delete** (lixeira) ou remova/ajuste os vínculos antes de apagar definitivamente."
                        )
                    except Exception as e:
                        st.error(f"Erro ao limpar: {e}")

            # ---------------------------------------------
            # Deduplicação: Grupos e Departamentos
            # ---------------------------------------------
            admin_divider()
            st.markdown("### Deduplicar Grupos e Departamentos")
            st.caption(
                "Remove duplicidades por **nome** (ignorando maiúsculas/minúsculas e espaços). "
                "Antes de apagar duplicados, o sistema **reaponta** os vínculos em `equipamentos` (e `equip_grupos`, quando aplicável)."
            )

            import re
            import unicodedata

            def _strip_accents(s: str) -> str:
                # Remove acentos: "São" -> "Sao"
                return "".join(
                    ch
                    for ch in unicodedata.normalize("NFKD", s)
                    if not unicodedata.combining(ch)
                )

            def _norm_name(x: str | None) -> str:
                """Normaliza nomes para detecção robusta de duplicidades.

                - lower
                - remove acentos
                - remove pontuação (mantém letras/números/espaço)
                - compacta espaços
                """
                s = (x or "").strip().lower()
                if not s:
                    return ""
                s = _strip_accents(s)
                s = re.sub(r"[^a-z0-9\s]", " ", s)
                s = " ".join(s.split())
                return s

            def _pick_canonical(rows: list[dict], key_created_at: str = "created_at") -> dict:
                """Escolhe o registro 'principal' (mais antigo) e retorna ele."""
                def _sort_key(r: dict):
                    return (str(r.get(key_created_at) or ""), str(r.get("id") or ""))

                return sorted(rows, key=_sort_key)[0]

            def _chunked_ids(ids: list[str], n: int = 200):
                for i in range(0, len(ids), n):
                    yield ids[i : i + n]

            def _try_hard_delete(table: str, ids: list[str]) -> tuple[int, list[str]]:
                """Tenta hard delete em lote. Retorna (apagados, ids_falharam)."""
                if not ids:
                    return 0, []
                deleted = 0
                failed: list[str] = []
                for chunk in _chunked_ids(ids, 200):
                    try:
                        sb.table(table).delete().eq("tenant_id", tenant_id).in_("id", chunk).execute()
                        deleted += len(chunk)
                    except Exception:
                        failed.extend(chunk)
                return deleted, failed

            def _soft_deactivate(table: str, ids: list[str]) -> int:
                if not ids:
                    return 0
                updated = 0
                for chunk in _chunked_ids(ids, 200):
                    try:
                        sb.table(table).update({"ativo": False}).eq("tenant_id", tenant_id).in_("id", chunk).execute()
                        updated += len(chunk)
                    except Exception:
                        pass
                return updated

            # --- Importante: se não estiver usando service role, deletes/updates podem falhar por RLS.
            # Mostra aviso explícito para não parecer que "não funcionou".
            try:
                _using_service_role = callable(get_supabase_service) and (sb is not None) and (sb is get_supabase_service())
            except Exception:
                _using_service_role = False

            if not _using_service_role:
                st.warning(
                    "⚠️ **Você não está usando o client Service Role aqui.** "
                    "Se o seu Supabase tiver RLS ativo para `departamentos`/`equip_grupos`, "
                    "as ações de **deduplicar/limpar** podem ser bloqueadas e nada muda. "
                    "\n\n➡️ Verifique se o `SUPABASE_SERVICE_ROLE_KEY` está configurado no `secrets.toml` (Streamlit Cloud) "
                    "e se você está logado como **admin/superadmin**."
                )

            # Carrega dados (best-effort)
            try:
                deps_all = (
                    sb.table("departamentos")
                    .select("id,nome,ativo,created_at")
                    .eq("tenant_id", tenant_id)
                    .order("nome")
                    .execute()
                    .data
                ) or []
            except Exception:
                deps_all = []

            try:
                grps_all = (
                    sb.table("equip_grupos")
                    .select("id,nome,ativo,created_at,departamento_id")
                    .eq("tenant_id", tenant_id)
                    .order("nome")
                    .execute()
                    .data
                ) or []
            except Exception:
                # Fallback se a coluna departamento_id não existir
                try:
                    grps_all = (
                        sb.table("equip_grupos")
                        .select("id,nome,ativo,created_at")
                        .eq("tenant_id", tenant_id)
                        .order("nome")
                        .execute()
                        .data
                    ) or []
                except Exception:
                    grps_all = []

            # Detecta duplicidades
            dep_dups: dict[str, list[dict]] = {}
            for d in deps_all:
                k = _norm_name(d.get("nome"))
                if not k:
                    continue
                dep_dups.setdefault(k, []).append(d)
            dep_dups = {k: v for k, v in dep_dups.items() if len(v) > 1}

            # Para grupos, por padrão consideramos (departamento_id + nome) como chave.
            # Se o cliente tiver duplicidade "global" (mesmo nome em departamentos diferentes),
            # ele pode desligar esta opção para deduplicar pelo nome apenas.
            consider_depto = st.checkbox(
                "Considerar Departamento ao deduplicar Grupos (recomendado)",
                value=True,
                key="eq_dedupe_consider_depto",
                help="Se desligar, grupos com o mesmo nome serão unificados mesmo que pertençam a departamentos diferentes.",
            )

            grp_dups: dict[tuple[str | None, str], list[dict]] = {}
            for g in grps_all:
                k = _norm_name(g.get("nome"))
                if not k:
                    continue
                dep_id = (g.get("departamento_id") if isinstance(g, dict) else None) if consider_depto else None
                grp_dups.setdefault((dep_id, k), []).append(g)
            grp_dups = {k: v for k, v in grp_dups.items() if len(v) > 1}

            kpi1, kpi2, kpi3 = st.columns(3)
            kpi1.metric("Departamentos duplicados", len(dep_dups))
            kpi2.metric("Grupos duplicados", len(grp_dups))
            kpi3.metric("Total de duplicidades", len(dep_dups) + len(grp_dups))

            with st.expander("Ver amostra das duplicidades", expanded=False):
                if dep_dups:
                    rows = []
                    for k, items in list(dep_dups.items())[:20]:
                        rows.append({
                            "nome_normalizado": k,
                            "ocorrências": len(items),
                            "ids": ", ".join([str(i.get("id"))[:8] for i in items]),
                        })
                    st.dataframe(pd.DataFrame(rows), use_container_width=True)
                else:
                    st.info("Nenhuma duplicidade de departamentos encontrada.")

                if grp_dups:
                    rows = []
                    for (dep_id, k), items in list(grp_dups.items())[:20]:
                        rows.append({
                            "departamento_id": dep_id or "(sem)",
                            "nome_normalizado": k,
                            "ocorrências": len(items),
                            "ids": ", ".join([str(i.get("id"))[:8] for i in items]),
                        })
                    st.dataframe(pd.DataFrame(rows), use_container_width=True)
                else:
                    st.info("Nenhuma duplicidade de grupos encontrada.")

            st.markdown("#### Executar deduplicação")
            st.caption(
                "O processo mantém 1 registro por nome e tenta apagar os outros. "
                "Se o banco bloquear o delete (constraints), os duplicados são apenas **desativados** (`ativo=false`)."
            )

            phrase_d = st.text_input(
                "Para confirmar, digite **DEDUPLICAR**",
                value="",
                key="eq_dedupe_phrase",
                help="Evita ações acidentais.",
            )
            confirm_d = st.checkbox(
                "Entendo que esta ação altera vínculos e pode apagar registros duplicados",
                key="eq_dedupe_confirm",
            )

            can_run = (phrase_d.strip().upper() == "DEDUPLICAR") and confirm_d
            if st.button("Deduplicar agora", use_container_width=True, disabled=not can_run, key="eq_dedupe_run"):
                # 1) Departamentos: atualiza vínculos, depois remove duplicados
                dep_report = {"hard_deleted": 0, "soft_disabled": 0}
                for _k, items in dep_dups.items():
                    canonical = _pick_canonical(items)
                    canon_id = canonical.get("id")
                    dup_ids = [i.get("id") for i in items if i.get("id") and i.get("id") != canon_id]
                    dup_ids = [x for x in dup_ids if x]
                    if not canon_id or not dup_ids:
                        continue
                    # Reaponta equipamentos.departamento_id
                    for did in dup_ids:
                        try:
                            sb.table("equipamentos").update({"departamento_id": canon_id}).eq("tenant_id", tenant_id).eq("departamento_id", did).execute()
                        except APIError as e:
                            st.error(f"Falha ao reapontar equipamentos.departamento_id (de {did} -> {canon_id}): {getattr(e, 'message', str(e))}")
                            try:
                                st.json(e.json())
                            except Exception:
                                pass
                        except Exception as e:
                            st.error(f"Falha ao reapontar equipamentos.departamento_id (de {did} -> {canon_id}): {e}")
                    # Reaponta equip_grupos.departamento_id (se existir)
                    try:
                        for did in dup_ids:
                            sb.table("equip_grupos").update({"departamento_id": canon_id}).eq("tenant_id", tenant_id).eq("departamento_id", did).execute()
                    except APIError:
                        # Se não existir coluna/perm, não para o fluxo; só registra para debug.
                        st.info("Não foi possível atualizar equip_grupos.departamento_id (coluna inexistente ou bloqueio por permissão/RLS).")
                    except Exception:
                        st.info("Não foi possível atualizar equip_grupos.departamento_id (coluna inexistente ou bloqueio por permissão/RLS).")

                    # Tenta apagar duplicados
                    hard_deleted, failed = _try_hard_delete("departamentos", dup_ids)
                    dep_report["hard_deleted"] += hard_deleted
                    if failed:
                        dep_report["soft_disabled"] += _soft_deactivate("departamentos", failed)

                # 2) Grupos: atualiza vínculos em equipamentos.grupo_id, depois remove duplicados
                grp_report = {"hard_deleted": 0, "soft_disabled": 0}
                for (_dep_id, _k), items in grp_dups.items():
                    canonical = _pick_canonical(items)
                    canon_id = canonical.get("id")
                    dup_ids = [i.get("id") for i in items if i.get("id") and i.get("id") != canon_id]
                    dup_ids = [x for x in dup_ids if x]
                    if not canon_id or not dup_ids:
                        continue
                    for gid in dup_ids:
                        try:
                            sb.table("equipamentos").update({"grupo_id": canon_id}).eq("tenant_id", tenant_id).eq("grupo_id", gid).execute()
                        except APIError as e:
                            st.error(f"Falha ao reapontar equipamentos.grupo_id (de {gid} -> {canon_id}): {getattr(e, 'message', str(e))}")
                            try:
                                st.json(e.json())
                            except Exception:
                                pass
                        except Exception as e:
                            st.error(f"Falha ao reapontar equipamentos.grupo_id (de {gid} -> {canon_id}): {e}")
                    hard_deleted, failed = _try_hard_delete("equip_grupos", dup_ids)
                    grp_report["hard_deleted"] += hard_deleted
                    if failed:
                        grp_report["soft_disabled"] += _soft_deactivate("equip_grupos", failed)

                try:
                    _audit(
                        sb,
                        tenant_id,
                        "dedupe",
                        {
                            "departamentos": dep_report,
                            "grupos": grp_report,
                            "found": {"dep_sets": len(dep_dups), "grp_sets": len(grp_dups)},
                        },
                    )
                except Exception:
                    pass

                st.success(
                    "Deduplicação concluída. "
                    f"Departamentos: apagados={dep_report['hard_deleted']}, desativados={dep_report['soft_disabled']}. "
                    f"Grupos: apagados={grp_report['hard_deleted']}, desativados={grp_report['soft_disabled']}."
                )
                _rerun()

    # ----- TAB 3: HISTÓRICO / AUDITORIA