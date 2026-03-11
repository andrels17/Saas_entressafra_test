"""Tab 2 — Gerenciar usuários: role, escopo, senha, remoção."""
import streamlit as st
from postgrest.exceptions import APIError

ROLES = ["admin", "gestor", "executor", "viewer"]
ROLE_ICONS = {"admin": "🔴", "gestor": "🟠", "executor": "🟡", "viewer": "⚪"}


def _load_tenant_users(svc, tenant_id: str) -> list[dict]:
    try:
        rows = (
            svc.table("tenant_users")
            .select("user_id, role, user_profiles(nome)")
            .eq("tenant_id", tenant_id)
            .order("role")
            .execute()
            .data
        ) or []
        return rows
    except APIError:
        rows = (
            svc.table("tenant_users")
            .select("user_id, role")
            .eq("tenant_id", tenant_id)
            .order("role")
            .execute()
            .data
        ) or []
        try:
            ids = [r.get("user_id") for r in rows if r.get("user_id")]
            profs = (
                svc.table("user_profiles").select("user_id, nome").in_("user_id", ids).execute().data
            ) or [] if ids else []
            nome_map = {p.get("user_id"): p.get("nome") for p in profs}
            for r in rows:
                r["user_profiles"] = {"nome": nome_map.get(r.get("user_id"))}
        except Exception:
            pass
        return rows


def _load_user_scope_multi(svc, tenant_id: str, user_id: str) -> dict:
    out = {"departamento_ids": [], "grupo_id": None}
    try:
        rows = (
            svc.table("tenant_user_departamentos")
            .select("departamento_id,grupo_id")
            .eq("tenant_id", tenant_id)
            .eq("user_id", user_id)
            .execute()
            .data
        ) or []
        if rows:
            out["departamento_ids"] = [r.get("departamento_id") for r in rows if r.get("departamento_id")]
            out["grupo_id"] = next((r.get("grupo_id") for r in rows if r.get("grupo_id")), None)
            return out
    except Exception:
        pass
    try:
        row = (
            svc.table("tenant_user_scope")
            .select("departamento_id,grupo_id")
            .eq("tenant_id", tenant_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
            .data
        ) or []
        if row:
            d = row[0].get("departamento_id")
            g = row[0].get("grupo_id")
            out["departamento_ids"] = [d] if d else []
            out["grupo_id"] = g
    except Exception:
        pass
    return out


def _save_user_scope_multi(svc, tenant_id: str, user_id: str, departamento_ids: list, grupo_id):
    departamento_ids = [d for d in (departamento_ids or []) if d]
    new_ok = False
    try:
        svc.table("tenant_user_departamentos").delete().eq("tenant_id", tenant_id).eq("user_id", user_id).execute()
        if departamento_ids:
            payload = [
                {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "departamento_id": d,
                    "grupo_id": (grupo_id if len(departamento_ids) == 1 else None),
                }
                for d in departamento_ids
            ]
            svc.table("tenant_user_departamentos").insert(payload).execute()
        new_ok = True
    except Exception:
        new_ok = False

    if len(departamento_ids) == 1:
        pl = {"tenant_id": tenant_id, "user_id": user_id,
              "departamento_id": departamento_ids[0], "grupo_id": grupo_id}
        try:
            svc.table("tenant_user_scope").upsert(pl, on_conflict="tenant_id,user_id").execute()
        except Exception:
            try:
                svc.table("tenant_user_scope").upsert(pl).execute()
            except Exception:
                pass
    else:
        try:
            svc.table("tenant_user_scope").delete().eq("tenant_id", tenant_id).eq("user_id", user_id).execute()
        except Exception:
            pass

    if not new_ok:
        d0 = departamento_ids[0] if departamento_ids else None
        if not d0:
            try:
                svc.table("tenant_user_scope").delete().eq("tenant_id", tenant_id).eq("user_id", user_id).execute()
            except Exception:
                pass
            return
        pl = {"tenant_id": tenant_id, "user_id": user_id, "departamento_id": d0, "grupo_id": grupo_id}
        try:
            svc.table("tenant_user_scope").upsert(pl, on_conflict="tenant_id,user_id").execute()
        except Exception:
            svc.table("tenant_user_scope").upsert(pl).execute()


def _clear_user_scope(svc, tenant_id: str, user_id: str):
    """Remove todos os vínculos de departamento e grupo do usuário."""
    try:
        svc.table("tenant_user_departamentos").delete().eq("tenant_id", tenant_id).eq("user_id", user_id).execute()
    except Exception:
        pass
    try:
        svc.table("tenant_user_scope").delete().eq("tenant_id", tenant_id).eq("user_id", user_id).execute()
    except Exception:
        pass


def _load_departamentos(svc, tenant_id: str) -> list[dict]:
    try:
        return (
            svc.table("departamentos").select("id,nome,ativo")
            .eq("tenant_id", tenant_id).eq("ativo", True).order("nome").execute().data
        ) or []
    except Exception:
        return []


def _load_grupos(svc, tenant_id: str) -> list[dict]:
    try:
        return (
            svc.table("equip_grupos").select("id,nome,ativo,departamento_id")
            .eq("tenant_id", tenant_id).eq("ativo", True).order("nome").execute().data
        ) or []
    except Exception:
        try:
            return (
                svc.table("equip_grupos").select("id,nome,ativo")
                .eq("tenant_id", tenant_id).eq("ativo", True).order("nome").execute().data
            ) or []
        except Exception:
            return []


def _get_user_label(u: dict) -> str:
    nome = (u.get("user_profiles") or {}).get("nome") or "—"
    role = u.get("role") or "—"
    icon = ROLE_ICONS.get(role, "⚪")
    return f"{icon} {nome} ({role})"


def render_tab_gerenciar(svc, tenant_id: str, rerun_fn, safe_json_fn):
    users = _load_tenant_users(svc, tenant_id)
    if not users:
        st.info("Nenhum usuário no tenant.")
        st.stop()

    # ── Seleção de usuário ────────────────────────────────────────────────────
    labels = [_get_user_label(u) for u in users]
    label_to_user = {labels[i]: users[i] for i in range(len(labels))}
    sel_label = st.selectbox("👤 Selecione um usuário", labels, key="ger_user_sel")
    sel_user = label_to_user[sel_label]
    target_user_id = sel_user["user_id"]

    cur_nome = (sel_user.get("user_profiles") or {}).get("nome") or ""
    cur_role = sel_user.get("role") or "viewer"
    if cur_role not in ROLES:
        cur_role = "viewer"

    st.divider()
    col_esq, col_dir = st.columns([1, 1], gap="large")

    # ── COLUNA ESQUERDA: Perfil + Senha ───────────────────────────────────────
    with col_esq:
        st.markdown("#### ✏️ Perfil e role")
        with st.form("update_user_form"):
            new_nome = st.text_input("Nome", value=cur_nome, placeholder="Nome completo")
            new_role = st.selectbox(
                "Role no tenant", ROLES, index=ROLES.index(cur_role),
                format_func=lambda r: f"{ROLE_ICONS.get(r, '')} {r.capitalize()}",
            )
            if st.form_submit_button("💾 Salvar perfil", type="primary", use_container_width=True):
                try:
                    try:
                        svc.table("user_profiles").upsert({"user_id": target_user_id, "nome": new_nome}).execute()
                    except Exception:
                        pass
                    svc.table("tenant_users").upsert(
                        {"tenant_id": tenant_id, "user_id": target_user_id, "role": new_role}
                    ).execute()
                    st.success("✅ Perfil atualizado!")
                    rerun_fn()
                except Exception as e:
                    st.error("Erro ao salvar.")
                    st.json(safe_json_fn(e))

        st.markdown("#### 🔑 Trocar senha")
        with st.form("reset_password_form"):
            p1 = st.text_input("Nova senha", type="password", placeholder="Mínimo 6 caracteres")
            p2 = st.text_input("Confirmar senha", type="password")
            do_reset = st.form_submit_button("🔒 Atualizar senha", use_container_width=True)

        if do_reset:
            if not p1 or len(p1) < 6:
                st.warning("A senha deve ter pelo menos 6 caracteres.")
            elif p1 != p2:
                st.warning("As senhas não conferem.")
            else:
                try:
                    svc.auth.admin.update_user_by_id(target_user_id, {"password": p1})
                    st.success("✅ Senha atualizada.")
                except Exception as e:
                    st.error("Erro ao atualizar senha.")
                    st.json(safe_json_fn(e))

    # ── COLUNA DIREITA: Escopo + Remover ──────────────────────────────────────
    with col_dir:
        st.markdown("#### 🏢 Escopo (departamento / grupo)")
        st.caption("Define o que o usuário enxerga no sistema. Admin sempre vê tudo.")

        depts = _load_departamentos(svc, tenant_id)
        grupos_all = _load_grupos(svc, tenant_id)
        cur_scope = _load_user_scope_multi(svc, tenant_id, target_user_id)
        cur_deps = cur_scope.get("departamento_ids") or []
        cur_grp = cur_scope.get("grupo_id")

        if not depts:
            st.info("Cadastre departamentos para habilitar o escopo.")
        else:
            dept_map = {d["nome"]: d["id"] for d in depts}
            dept_names = [d["nome"] for d in depts]

            sel_dept_names = st.multiselect(
                "Departamentos", dept_names,
                default=[n for n in dept_names if dept_map.get(n) in set(cur_deps)],
                key=f"scope_deps_{target_user_id}",
                placeholder="Selecione um ou mais departamentos…",
            )
            sel_dep_ids = [dept_map[n] for n in sel_dept_names if dept_map.get(n)]

            sel_grp_id = None
            if len(sel_dep_ids) == 1:
                dep_id_single = sel_dep_ids[0]
                grupos_filtered = [g for g in grupos_all if (
                    not g.get("departamento_id") or g.get("departamento_id") == dep_id_single
                )]
                grp_opts  = [{"id": None, "nome": "Todos os grupos do departamento"}] + grupos_filtered
                grp_names = [g["nome"] for g in grp_opts]
                grp_ids   = [g["id"]   for g in grp_opts]
                grp_idx   = grp_ids.index(cur_grp) if cur_grp in grp_ids else 0
                sel_grp_name = st.selectbox("Grupo", grp_names, index=grp_idx, key=f"scope_grp_{target_user_id}")
                sel_grp_id = grp_ids[grp_names.index(sel_grp_name)]
            elif len(sel_dep_ids) > 1:
                st.caption("Múltiplos departamentos — todos os grupos incluídos.")

            # Mostra vínculo atual
            if cur_deps:
                cur_dep_nomes = [d["nome"] for d in depts if d["id"] in cur_deps]
                cur_grp_nome  = next((g["nome"] for g in grupos_all if g["id"] == cur_grp), None)
                partes = ", ".join(cur_dep_nomes)
                if cur_grp_nome:
                    partes += f" › {cur_grp_nome}"
                st.caption(f"Vínculo atual: **{partes}**")
            else:
                st.caption("Sem vínculo definido.")

            col_salvar, col_desvincular = st.columns(2)
            with col_salvar:
                if st.button("💾 Salvar escopo", type="primary", use_container_width=True,
                             key=f"scope_save_{target_user_id}"):
                    try:
                        _save_user_scope_multi(svc, tenant_id, target_user_id, sel_dep_ids, sel_grp_id)
                        st.success("✅ Escopo salvo.")
                        rerun_fn()
                    except Exception as e:
                        st.error("Erro ao salvar escopo.")
                        st.json(safe_json_fn(e))

            with col_desvincular:
                if st.button(
                    "🔗 Desvincular tudo",
                    use_container_width=True,
                    key=f"scope_clear_{target_user_id}",
                    disabled=not bool(cur_deps),
                    help="Remove todos os vínculos de departamento e grupo deste usuário.",
                ):
                    try:
                        _clear_user_scope(svc, tenant_id, target_user_id)
                        st.success("✅ Vínculos removidos.")
                        rerun_fn()
                    except Exception as e:
                        st.error("Erro ao desvincular.")
                        st.json(safe_json_fn(e))

        st.markdown("#### 🗑️ Remover do tenant")
        st.caption("Remove o vínculo deste usuário com o tenant. Não apaga a conta.")

        confirm_rm = st.checkbox(
            "Confirmo remover este usuário do tenant", value=False,
            key=f"confirm_rm_{target_user_id}",
        )
        if st.button("Remover vínculo do tenant", use_container_width=True,
                     disabled=not confirm_rm, key=f"rm_user_{target_user_id}"):
            try:
                svc.table("tenant_users").delete().eq("tenant_id", tenant_id).eq("user_id", target_user_id).execute()
                try:
                    svc.table("user_setores").delete().eq("tenant_id", tenant_id).eq("user_id", target_user_id).execute()
                except Exception:
                    pass
                _clear_user_scope(svc, tenant_id, target_user_id)
                st.success("✅ Vínculo removido.")
                rerun_fn()
            except Exception as e:
                st.error("Erro ao remover vínculo.")
                st.json(safe_json_fn(e))
