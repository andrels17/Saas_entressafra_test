"""Tab 2 — Gerenciar usuários: role, escopo, senha, remoção."""
import logging
import streamlit as st
from postgrest.exceptions import APIError
from src.auth.audit import audit_user_role_changed, audit_user_deleted

ROLES = ["admin", "supervisor", "gestor", "executor", "viewer"]
ROLE_ICONS = {
    "admin": "🔴",
    "supervisor": "🟣",
    "gestor": "🟠",
    "executor": "🟡",
    "viewer": "⚪"}



log = logging.getLogger("saas.admin.gerenciar")

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
            profs = (svc.table("user_profiles").select("user_id, nome").in_(
                "user_id", ids).execute().data) or [] if ids else []
            nome_map = {p.get("user_id"): p.get("nome") for p in profs}
            for r in rows:
                r["user_profiles"] = {"nome": nome_map.get(r.get("user_id"))}
        except Exception as _e:
            import logging; logging.getLogger("saas").warning("gerenciar.py: %s", _e)
        return rows


def _load_user_scope_multi(svc, tenant_id: str, user_id: str) -> dict:
    """Carrega escopo multi-vínculo usando apenas tenant_user_departamentos.

    Estrutura esperada:
    - uma linha pode representar somente departamento: grupo_id = NULL
    - uma linha pode representar grupo específico: departamento_id + grupo_id
    """
    out = {"departamento_ids": [], "grupo_ids": []}
    try:
        rows = (
            svc.table("tenant_user_departamentos")
            .select("departamento_id,grupo_id")
            .eq("tenant_id", tenant_id)
            .eq("user_id", user_id)
            .execute()
            .data
        ) or []

        dep_seen = set()
        grp_seen = set()
        for r in rows:
            dep_id = r.get("departamento_id")
            grp_id = r.get("grupo_id")

            if dep_id and dep_id not in dep_seen:
                dep_seen.add(dep_id)
                out["departamento_ids"].append(dep_id)

            if grp_id and grp_id not in grp_seen:
                grp_seen.add(grp_id)
                out["grupo_ids"].append(grp_id)

    except Exception as _e:
        log.warning("Erro ao carregar escopo multi do usuário: %s", _e)

    return out


def _save_user_scope_multi(
    svc,
    tenant_id: str,
    user_id: str,
    departamento_ids: list | None,
    grupo_ids: list | None = None,
    grupo_departamento_ids: dict | None = None,
):
    """Salva escopo N:N sem fallback legado.

    A fonte oficial dos vínculos passa a ser tenant_user_departamentos.
    Para evitar conflito com a estrutura antiga, tenant_user_scope é apagada
    para este usuário sempre que o escopo for salvo.
    """
    departamento_ids = [d for d in (departamento_ids or []) if d]

    if grupo_ids is None:
        grupo_ids = []
    elif not isinstance(grupo_ids, (list, tuple, set)):
        grupo_ids = [grupo_ids]
    grupo_ids = [g for g in grupo_ids if g]

    grupo_departamento_ids = grupo_departamento_ids or {}

    # Garante que todo grupo selecionado também inclua seu departamento.
    dep_seen = set(departamento_ids)
    for gid in grupo_ids:
        dep_id = grupo_departamento_ids.get(gid) or grupo_departamento_ids.get(str(gid))
        if dep_id and dep_id not in dep_seen:
            departamento_ids.append(dep_id)
            dep_seen.add(dep_id)

    # Regrava tudo para não manter vínculo antigo/stale.
    svc.table("tenant_user_departamentos").delete().eq(
        "tenant_id", tenant_id
    ).eq("user_id", user_id).execute()

    # Remove registro legado para evitar leitura/relatório usando dado antigo.
    try:
        svc.table("tenant_user_scope").delete().eq(
            "tenant_id", tenant_id
        ).eq("user_id", user_id).execute()
    except Exception as _e:
        log.warning("Não foi possível limpar tenant_user_scope legado: %s", _e)

    payload = []

    # Monta primeiro os vínculos específicos por grupo.
    # Importante: a PK antiga da tabela era por departamento; então não podemos
    # inserir uma linha "departamento puro" e outra linha de grupo para o mesmo
    # departamento. Além disso, para múltiplos grupos no mesmo departamento, o
    # banco precisa estar com a migração SQL incluída neste pacote.
    deps_com_grupo = set()
    seen_group_rows = set()

    for gid in grupo_ids:
        dep_id = grupo_departamento_ids.get(gid) or grupo_departamento_ids.get(str(gid))
        if not dep_id:
            continue

        deps_com_grupo.add(dep_id)
        row_key = (str(dep_id), str(gid))
        if row_key in seen_group_rows:
            continue

        seen_group_rows.add(row_key)
        payload.append({
            "tenant_id": tenant_id,
            "user_id": user_id,
            "departamento_id": dep_id,
            "grupo_id": gid,
        })

    # Salva somente departamentos sem grupo específico selecionado.
    # Se o departamento já aparece por grupo, a leitura do escopo continua
    # reconhecendo o departamento através da própria linha do grupo.
    seen_dept_rows = set()
    for dep_id in departamento_ids:
        if dep_id in deps_com_grupo:
            continue
        row_key = str(dep_id)
        if row_key in seen_dept_rows:
            continue

        seen_dept_rows.add(row_key)
        payload.append({
            "tenant_id": tenant_id,
            "user_id": user_id,
            "departamento_id": dep_id,
            "grupo_id": None,
        })

    if payload:
        svc.table("tenant_user_departamentos").insert(payload).execute()


def _clear_user_scope(svc, tenant_id: str, user_id: str):
    """Remove todos os vínculos de departamento e grupo do usuário."""
    try:
        svc.table("tenant_user_departamentos").delete().eq(
            "tenant_id", tenant_id
        ).eq("user_id", user_id).execute()
    except Exception as _e:
        log.warning("Erro ao limpar tenant_user_departamentos: %s", _e)

    # Limpeza defensiva da tabela legada, para não ressuscitar vínculo antigo em outros módulos.
    try:
        svc.table("tenant_user_scope").delete().eq(
            "tenant_id", tenant_id
        ).eq("user_id", user_id).execute()
    except Exception as _e:
        log.warning("Erro ao limpar tenant_user_scope legado: %s", _e)


def _remove_user_scope_items(
    svc,
    tenant_id: str,
    user_id: str,
    departamento_ids: list | None = None,
    grupo_ids: list | None = None,
):
    """Remove somente os vínculos selecionados.

    - Departamento remove o departamento e todos os grupos vinculados nele.
    - Grupo remove apenas aquele grupo, mantendo o departamento.
    """
    departamento_ids = [d for d in (departamento_ids or []) if d]
    grupo_ids = [g for g in (grupo_ids or []) if g]

    try:
        for dep_id in departamento_ids:
            svc.table("tenant_user_departamentos").delete().eq(
                "tenant_id", tenant_id
            ).eq("user_id", user_id).eq("departamento_id", dep_id).execute()

        for gid in grupo_ids:
            svc.table("tenant_user_departamentos").delete().eq(
                "tenant_id", tenant_id
            ).eq("user_id", user_id).eq("grupo_id", gid).execute()

        # Limpa legado para evitar exibição/salvamento com vínculo antigo.
        try:
            svc.table("tenant_user_scope").delete().eq(
                "tenant_id", tenant_id
            ).eq("user_id", user_id).execute()
        except Exception as _e:
            log.warning("Erro ao limpar tenant_user_scope legado: %s", _e)

    except Exception as _e:
        log.warning("Erro ao remover vínculos específicos: %s", _e)


def _load_departamentos(svc, tenant_id: str) -> list[dict]:
    try:
        return (
            svc.table("departamentos").select("id,nome,ativo") .eq(
                "tenant_id",
                tenant_id).eq(
                "ativo",
                True).order("nome").execute().data) or []
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
                svc.table("equip_grupos").select("id,nome,ativo") .eq(
                    "tenant_id", tenant_id).eq(
                    "ativo", True).order("nome").execute().data) or []
        except Exception:
            return []


def _get_user_label(u: dict) -> str:
    nome = (u.get("user_profiles") or {}).get("nome") or "—"
    role = u.get("role") or "—"
    icon = ROLE_ICONS.get(role, "⚪")
    return f"{icon} {nome} ({role})"


def render_tab_gerenciar(svc, tenant_id: str, rerun_fn, safe_json_fn) -> None:
    users = _load_tenant_users(svc, tenant_id)
    if not users:
        st.info("Nenhum usuário no tenant.")
        st.stop()

    # ── Seleção de usuário ──────────────────────────────────────────────────
    labels = [_get_user_label(u) for u in users]
    label_to_user = {labels[i]: users[i] for i in range(len(labels))}
    sel_label = st.selectbox(
        "👤 Selecione um usuário",
        labels,
        key="ger_user_sel")
    sel_user = label_to_user[sel_label]
    target_user_id = sel_user["user_id"]

    cur_nome = (sel_user.get("user_profiles") or {}).get("nome") or ""
    cur_role = sel_user.get("role") or "viewer"
    if cur_role not in ROLES:
        cur_role = "viewer"

    st.divider()
    col_esq, col_dir = st.columns([1, 1], gap="large")

    # ── COLUNA ESQUERDA: Perfil + Senha ─────────────────────────────────────
    with col_esq:
        st.markdown("#### ✏️ Perfil e role")
        with st.form("update_user_form"):
            new_nome = st.text_input(
                "Nome", value=cur_nome, placeholder="Nome completo")
            new_role = st.selectbox(
                "Role no tenant",
                ROLES,
                index=ROLES.index(cur_role),
                format_func=lambda r: f"{
                    ROLE_ICONS.get(
                        r,
                        '')} {
                    r.capitalize()}",
            )
            if st.form_submit_button(
                "💾 Salvar perfil",
                type="primary",
                    use_container_width=True):
                try:
                    try:
                        svc.table("user_profiles").upsert(
                            {"user_id": target_user_id, "nome": new_nome}).execute()
                    except Exception as _e:
                        import logging; logging.getLogger("saas").warning("gerenciar.py: %s", _e)
                    svc.table("tenant_users").upsert(
                        {"tenant_id": tenant_id, "user_id": target_user_id, "role": new_role}
                    ).execute()
                    if new_role != cur_role:
                        audit_user_role_changed(
                            target_user_id, cur_role, new_role)
                    st.success("✅ Perfil atualizado!")
                    rerun_fn()
                except Exception as e:
                    st.error("Erro ao salvar.")
                    st.json(safe_json_fn(e))

        st.markdown("#### 🔑 Trocar senha")
        with st.form("reset_password_form"):
            p1 = st.text_input(
                "Nova senha",
                type="password",
                placeholder="Mínimo 6 caracteres")
            p2 = st.text_input("Confirmar senha", type="password")
            do_reset = st.form_submit_button(
                "🔒 Atualizar senha", use_container_width=True)

        if do_reset:
            if not p1 or len(p1) < 6:
                st.warning("A senha deve ter pelo menos 6 caracteres.")
            elif p1 != p2:
                st.warning("As senhas não conferem.")
            else:
                try:
                    svc.auth.admin.update_user_by_id(
                        target_user_id, {"password": p1})
                    st.success("✅ Senha atualizada.")
                except Exception as e:
                    st.error("Erro ao atualizar senha.")
                    st.json(safe_json_fn(e))

    # ── COLUNA DIREITA: Escopo + Remover ────────────────────────────────────
    with col_dir:
        st.markdown("#### 🏢 Escopo (departamento / grupo)")
        st.caption(
            "Define o que o usuário enxerga no sistema. Admin sempre vê tudo.")

        depts = _load_departamentos(svc, tenant_id)
        grupos_all = _load_grupos(svc, tenant_id)
        cur_scope = _load_user_scope_multi(svc, tenant_id, target_user_id)
        cur_deps = cur_scope.get("departamento_ids") or []
        cur_grp_ids = cur_scope.get("grupo_ids") or []

        if not depts:
            st.info("Cadastre departamentos para habilitar o escopo.")
        else:
            dept_map = {d["nome"]: d["id"] for d in depts}
            dept_names = [d["nome"] for d in depts]

            sel_grp_ids = []
            sel_grp_dep_map = {}
            if cur_role == "gestor":
                st.info("Gestor pode ter vários departamentos e vários grupos vinculados.")

                sel_dept_names = st.multiselect(
                    "Departamentos",
                    dept_names,
                    default=[
                        n for n in dept_names if dept_map.get(n) in set(cur_deps)],
                    key=f"scope_deps_gestor_{target_user_id}",
                    placeholder="Selecione um ou mais departamentos…",
                )
                sel_dep_ids = [dept_map[n] for n in sel_dept_names if dept_map.get(n)]

                grupos_filtered = [
                    g for g in grupos_all
                    if not sel_dep_ids or str(g.get("departamento_id") or "") in {str(d) for d in sel_dep_ids}
                ]

                if not sel_dep_ids:
                    st.warning("Selecione pelo menos um departamento para habilitar os grupos do gestor.")
                    sel_grp_ids = []
                    sel_grp_dep_map = {}
                elif not grupos_filtered:
                    st.warning("Os departamentos selecionados ainda não possuem grupos ativos.")
                    sel_grp_ids = []
                    sel_grp_dep_map = {}
                else:
                    grp_labels = []
                    grp_label_to_obj = {}
                    dep_nome_by_id = {d["id"]: d["nome"] for d in depts}
                    for g in grupos_filtered:
                        dep_nome_g = dep_nome_by_id.get(g.get("departamento_id"), "Sem departamento")
                        label = f"{dep_nome_g} › {g['nome']}"
                        grp_labels.append(label)
                        grp_label_to_obj[label] = g

                    cur_grp_ids_set = set(cur_grp_ids)
                    default_grp_labels = [
                        lbl for lbl, g in grp_label_to_obj.items()
                        if g.get("id") in cur_grp_ids_set
                    ]
                    sel_grp_labels = st.multiselect(
                        "Grupos do gestor",
                        grp_labels,
                        default=default_grp_labels,
                        key=f"scope_grps_{target_user_id}",
                        placeholder="Selecione um ou mais grupos…",
                    )
                    sel_grp_objs = [grp_label_to_obj[lbl] for lbl in sel_grp_labels if lbl in grp_label_to_obj]
                    sel_grp_ids = [g.get("id") for g in sel_grp_objs if g.get("id")]
                    sel_grp_dep_map = {g.get("id"): g.get("departamento_id") for g in sel_grp_objs if g.get("id")}
                    # sel_grp_ids e sel_grp_dep_map ficam sempre como lista/dict para salvar corretamente.
            else:
                sel_dept_names = st.multiselect(
                    "Departamentos",
                    dept_names,
                    default=[
                        n for n in dept_names if dept_map.get(n) in set(cur_deps)],
                    key=f"scope_deps_{target_user_id}",
                    placeholder="Selecione um ou mais departamentos…",
                )
                sel_dep_ids = [dept_map[n]
                               for n in sel_dept_names if dept_map.get(n)]

                if len(sel_dep_ids) == 1:
                    dep_id_single = sel_dep_ids[0]
                    grupos_filtered = [
                        g for g in grupos_all if (
                            not g.get("departamento_id") or g.get("departamento_id") == dep_id_single)]
                    grp_opts = [
                        {"id": None, "nome": "Todos os grupos do departamento"}] + grupos_filtered
                    grp_names = [g["nome"] for g in grp_opts]
                    grp_ids = [g["id"] for g in grp_opts]
                    cur_grp_single = cur_grp_ids[0] if cur_grp_ids else None
                    grp_idx = grp_ids.index(cur_grp_single) if cur_grp_single in grp_ids else 0
                    sel_grp_name = st.selectbox(
                        "Grupo",
                        grp_names,
                        index=grp_idx,
                        key=f"scope_grp_{target_user_id}")
                    selected_gid = grp_ids[grp_names.index(sel_grp_name)]
                    if selected_gid:
                        sel_grp_ids = [selected_gid]
                        sel_grp_dep_map = {selected_gid: dep_id_single}
                elif len(sel_dep_ids) > 1:
                    st.caption(
                        "Múltiplos departamentos — todos os grupos incluídos.")

            # Mostra vínculo atual
            cur_dep_nomes = []
            cur_grp_ids = cur_scope.get("grupo_ids") or []
            cur_grp_nomes = []
            if cur_deps:
                cur_dep_nomes = [d["nome"] for d in depts if d["id"] in cur_deps]
            if cur_grp_ids:
                cur_grp_nomes = [g["nome"] for g in grupos_all if g["id"] in cur_grp_ids]

            if cur_dep_nomes or cur_grp_nomes:
                partes = ", ".join(cur_dep_nomes) if cur_dep_nomes else "Sem departamento"
                if cur_grp_nomes:
                    partes += " › " + " | ".join(cur_grp_nomes)
                st.caption(f"Vínculo atual: **{partes}**")
            else:
                st.caption("Sem vínculo definido.")

            if cur_deps or cur_grp_ids:
                remove_opts = []
                dep_label_to_id = {}
                grp_label_to_id = {}
                for dep in depts:
                    if dep["id"] in cur_deps:
                        label = f"Departamento: {dep['nome']}"
                        remove_opts.append(label)
                        dep_label_to_id[label] = dep["id"]

                for grp in grupos_all:
                    if grp["id"] in cur_grp_ids:
                        dep_nome = next((d["nome"] for d in depts if d["id"] == grp.get("departamento_id")), "Sem departamento")
                        label = f"Grupo: {dep_nome} › {grp['nome']}"
                        remove_opts.append(label)
                        grp_label_to_id[label] = grp["id"]

                selected_remove = st.multiselect(
                    "Remover vínculos específicos",
                    remove_opts,
                    key=f"scope_remove_items_{target_user_id}",
                    placeholder="Selecione departamento(s) e/ou grupo(s) para remover…",
                    help="Remove somente os vínculos escolhidos, sem apagar o usuário.",
                )

                if st.button(
                    "🧹 Remover selecionados",
                    use_container_width=True,
                    key=f"scope_remove_selected_{target_user_id}",
                    disabled=not bool(selected_remove),
                    help="Remove apenas os vínculos selecionados acima.",
                ):
                    try:
                        remove_dep_ids = [
                            dep_label_to_id[label]
                            for label in selected_remove
                            if label in dep_label_to_id
                        ]
                        remove_grp_ids = [
                            grp_label_to_id[label]
                            for label in selected_remove
                            if label in grp_label_to_id
                        ]
                        _remove_user_scope_items(
                            svc, tenant_id, target_user_id, remove_dep_ids, remove_grp_ids)
                        st.success("✅ Vínculos selecionados removidos.")
                        rerun_fn()
                    except Exception as e:
                        st.error("Erro ao remover vínculos selecionados.")
                        st.json(safe_json_fn(e))

            col_salvar, col_desvincular = st.columns(2)
            with col_salvar:
                if st.button(
                    "💾 Salvar escopo",
                    type="primary",
                    use_container_width=True,
                        disabled=(cur_role == "gestor" and not sel_dep_ids),
                        key=f"scope_save_{target_user_id}"):
                    try:
                        _save_user_scope_multi(
                            svc,
                            tenant_id,
                            target_user_id,
                            sel_dep_ids,
                            grupo_ids=sel_grp_ids,
                            grupo_departamento_ids=sel_grp_dep_map,
                        )
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
                    disabled=not bool(cur_deps or cur_grp_ids),
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
        st.caption(
            "Remove o vínculo deste usuário com o tenant. Não apaga a conta.")

        confirm_rm = st.checkbox(
            "Confirmo remover este usuário do tenant", value=False,
            key=f"confirm_rm_{target_user_id}",
        )
        if st.button("Remover vínculo do tenant", use_container_width=True,
                     disabled=not confirm_rm, key=f"rm_user_{target_user_id}"):
            try:
                svc.table("tenant_users").delete().eq(
                    "tenant_id", tenant_id).eq(
                    "user_id", target_user_id).execute()
                try:
                    svc.table("user_setores").delete().eq(
                        "tenant_id", tenant_id).eq(
                        "user_id", target_user_id).execute()
                except Exception as _e:
                    import logging; logging.getLogger("saas").warning("gerenciar.py: %s", _e)
                _clear_user_scope(svc, tenant_id, target_user_id)
                audit_user_deleted(target_user_id, cur_nome or target_user_id)
                st.success("✅ Vínculo removido.")
                rerun_fn()
            except Exception as e:
                st.error("Erro ao remover vínculo.")
                st.json(safe_json_fn(e))
