"""Tab 2 — Gerenciar usuários: role, escopo, senha, remoção."""
import streamlit as st
from postgrest.exceptions import APIError

ROLES = ["admin", "gestor", "executor", "viewer"]


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
    except APIError as e:
        st.warning("Não foi possível carregar nomes via relacionamento. Listando sem nomes.")
        st.caption(f"Detalhes PostgREST: {e}")
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


def _save_user_scope_multi(svc, tenant_id: str, user_id: str, departamento_ids: list[str], grupo_id: str | None):
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
        payload = {"tenant_id": tenant_id, "user_id": user_id, "departamento_id": departamento_ids[0], "grupo_id": grupo_id}
        try:
            svc.table("tenant_user_scope").upsert(payload, on_conflict="tenant_id,user_id").execute()
        except Exception:
            try:
                svc.table("tenant_user_scope").upsert(payload).execute()
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
        payload = {"tenant_id": tenant_id, "user_id": user_id, "departamento_id": d0, "grupo_id": grupo_id}
        try:
            svc.table("tenant_user_scope").upsert(payload, on_conflict="tenant_id,user_id").execute()
        except Exception:
            svc.table("tenant_user_scope").upsert(payload).execute()


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
    uid = u.get("user_id") or ""
    return f"{nome} — {role} — {uid[:8]}"


def render_tab_gerenciar(svc, tenant_id: str, rerun_fn, safe_json_fn):
    st.markdown("### Gerenciar usuários do tenant")
    users = _load_tenant_users(svc, tenant_id)
    if not users:
        st.info("Nenhum usuário no tenant.")
        st.stop()

    with st.expander("Lista rápida", expanded=False):
        for u in users:
            nome = (u.get("user_profiles") or {}).get("nome") or "—"
            st.markdown(
                f'<div class="card"><b>{nome}</b>'
                f'<div class="muted">{u["user_id"]}<br/>Role: {u["role"]}</div></div>',
                unsafe_allow_html=True,
            )

    labels = [_get_user_label(u) for u in users]
    label_to_user = {labels[i]: users[i] for i in range(len(labels))}
    sel_label = st.selectbox("Selecione um usuário", labels)
    sel_user = label_to_user[sel_label]
    target_user_id = sel_user["user_id"]

    st.divider()
    st.markdown("#### Editar dados do usuário")

    cur_nome = (sel_user.get("user_profiles") or {}).get("nome") or ""
    cur_role = sel_user.get("role") or "viewer"
    if cur_role not in ROLES:
        cur_role = "viewer"

    with st.form("update_user_form"):
        colA, colB = st.columns(2)
        with colA:
            new_nome = st.text_input("Nome", value=cur_nome)
        with colB:
            new_role = st.selectbox("Role no tenant", ROLES, index=ROLES.index(cur_role))
        submitted_update = st.form_submit_button("Salvar perfil/role", type="primary", use_container_width=True)

    if submitted_update:
        try:
            try:
                svc.table("user_profiles").upsert({"user_id": target_user_id, "nome": new_nome}).execute()
            except Exception:
                pass
            svc.table("tenant_users").upsert(
                {"tenant_id": tenant_id, "user_id": target_user_id, "role": new_role}
            ).execute()
            st.toast("✓ Atualizado", icon=":material/check_circle:")
            rerun_fn()
        except Exception as e:
            st.error("Erro ao salvar.")
            st.json(safe_json_fn(e))

    st.divider()
    st.markdown("#### Escopo do usuário (Departamento / Grupo)")
    st.caption("Define o que o usuário **não-admin** enxerga no sistema. Admin/Superadmin sempre veem tudo.")

    depts = _load_departamentos(svc, tenant_id)
    grupos_all = _load_grupos(svc, tenant_id)
    cur_scope = _load_user_scope_multi(svc, tenant_id, target_user_id) or {"departamento_ids": [], "grupo_id": None}
    cur_deps = cur_scope.get("departamento_ids") or []
    cur_grp = cur_scope.get("grupo_id")

    if not depts:
        st.info("Cadastre departamentos para habilitar o escopo por departamento.")
    else:
        dept_map = {d["nome"]: d["id"] for d in depts}
        dept_names = [d["nome"] for d in depts]
        is_gestor = (new_role == "gestor") if "new_role" in dir() else (cur_role == "gestor")

        cS1, cS2, cS3, cS4 = st.columns([1.25, 0.95, 1.15, 0.65])

        with cS1:
            sel_dept_names = st.multiselect(
                "Departamentos",
                dept_names,
                default=[n for n in dept_names if dept_map.get(n) in set(cur_deps)],
                key=f"scope_deps_{target_user_id}",
            )
            sel_dep_ids = [dept_map[n] for n in sel_dept_names if dept_map.get(n)]

        with cS2:
            all_groups = st.checkbox(
                "Todos os grupos",
                value=is_gestor if sel_dep_ids else False,
                disabled=not bool(sel_dep_ids),
                key=f"scope_allgrp_{target_user_id}",
            )

        with cS3:
            if sel_dep_ids and len(sel_dep_ids) == 1:
                dep_id_single = sel_dep_ids[0]
                if all_groups:
                    st.selectbox("Grupo", ["Todos os grupos do departamento"], index=0, disabled=True, key=f"scope_grp_all_{target_user_id}")
                    sel_grp_id = None
                else:
                    grupos_filtered = [g for g in grupos_all if (not g.get("departamento_id") or g.get("departamento_id") == dep_id_single)]
                    grp_opts = [{"id": None, "nome": "Todos os grupos do departamento"}] + grupos_filtered
                    grp_names = [g["nome"] for g in grp_opts]
                    grp_ids = [g["id"] for g in grp_opts]
                    grp_idx = grp_ids.index(cur_grp) if cur_grp in grp_ids else 0
                    sel_grp_name = st.selectbox("Grupo", grp_names, index=grp_idx, key=f"scope_grp_{target_user_id}")
                    sel_grp_id = grp_ids[grp_names.index(sel_grp_name)]
            elif sel_dep_ids and len(sel_dep_ids) > 1:
                st.selectbox("Grupo", ["(todos os grupos dos deptos selecionados)"], index=0, disabled=True, key=f"scope_grp_multi_{target_user_id}")
                sel_grp_id = None
            else:
                st.selectbox("Grupo", ["—"], disabled=True, key=f"scope_grp_disabled_{target_user_id}")
                sel_grp_id = None

        with cS4:
            if st.button("Salvar escopo", icon=":material/save:", type="primary", use_container_width=True, key=f"scope_save_{target_user_id}"):
                try:
                    _save_user_scope_multi(svc, tenant_id, target_user_id, sel_dep_ids, sel_grp_id)
                    st.success("Escopo salvo.")
                    rerun_fn()
                except Exception as e:
                    st.error("Erro ao salvar escopo.")
                    st.json(safe_json_fn(e))

    st.divider()
    st.markdown("#### Trocar senha (sem e-mail, sem link)")
    st.caption("Isso redefine a senha imediatamente no Auth.")

    with st.form("reset_password_form"):
        col1, col2 = st.columns(2)
        with col1:
            p1 = st.text_input("Nova senha", type="password")
        with col2:
            p2 = st.text_input("Confirmar nova senha", type="password")
        do_reset = st.form_submit_button("Atualizar senha", use_container_width=True)

    if do_reset:
        if not p1 or len(p1) < 6:
            st.warning("A senha deve ter pelo menos 6 caracteres.")
            st.stop()
        if p1 != p2:
            st.warning("As senhas não conferem.")
            st.stop()
        try:
            svc.auth.admin.update_user_by_id(target_user_id, {"password": p1})
            st.success("Senha atualizada.")
        except Exception as e:
            st.error("Erro ao atualizar senha.")
            st.json(safe_json_fn(e))

    st.divider()
    st.markdown("#### Remover do tenant (sem apagar usuário do Auth)")
    st.caption("Remove o vínculo do usuário com este tenant.")

    colR1, colR2 = st.columns(2)
    with colR1:
        confirm_rm = st.checkbox("Confirmo remover do tenant", value=False)
    with colR2:
        if st.button("Remover vínculo do tenant", use_container_width=True, disabled=not confirm_rm):
            try:
                svc.table("tenant_users").delete().eq("tenant_id", tenant_id).eq("user_id", target_user_id).execute()
                try:
                    svc.table("user_setores").delete().eq("tenant_id", tenant_id).eq("user_id", target_user_id).execute()
                except Exception:
                    pass
                st.success("Vínculo removido.")
                rerun_fn()
            except Exception as e:
                st.error("Erro ao remover vínculo.")
                st.json(safe_json_fn(e))
