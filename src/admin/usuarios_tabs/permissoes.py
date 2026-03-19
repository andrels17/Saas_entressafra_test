"""Tab 3 — Permissões por setor."""
import streamlit as st
from postgrest.exceptions import APIError

PERMS = ["nenhum", "view", "edit"]


def _get_user_label(u: dict) -> str:
    nome = (u.get("user_profiles") or {}).get("nome") or "—"
    role = u.get("role") or "—"
    uid = u.get("user_id") or ""
    return f"{nome} — {role} — {uid[:8]}"


def render_tab_permissoes(
        svc,
        tenant_id: str,
        users: list[dict],
        rerun_fn,
        safe_json_fn):
    st.markdown("### Permissões por setor")
    st.caption(
        "Defina quais setores o usuário pode **ver** ou **editar**. Admin/gestor normalmente não precisam disso.")

    if not users:
        st.info("Nenhum usuário no tenant.")
        st.stop()

    user_labels = [_get_user_label(u) for u in users]
    label_to_user = {user_labels[i]: users[i] for i in range(len(users))}
    sel_label = st.selectbox("Usuário", user_labels, key="perm_user_sel")
    sel_user = label_to_user[sel_label]
    target_user_id = sel_user["user_id"]

    try:
        setores = (
            svc.table("setores").select("id,nome") .eq(
                "tenant_id",
                tenant_id).eq(
                "ativo",
                True).order("nome").execute().data) or []
    except APIError as e:
        st.warning(
            "Não foi possível filtrar setores por 'ativo'. Listando todos.")
        st.caption(f"Detalhes PostgREST: {e}")
        setores = (
            svc.table("setores").select("id,nome")
            .eq("tenant_id", tenant_id).order("nome").execute().data
        ) or []

    if not setores:
        st.warning("Cadastre setores primeiro.")
        st.stop()

    perms = (
        svc.table("user_setores").select("setor_id,permissao") .eq(
            "tenant_id",
            tenant_id).eq(
            "user_id",
            target_user_id).execute().data) or []
    perm_map = {p["setor_id"]: p["permissao"] for p in perms}

    st.info("Escolha a permissão por setor e clique em **Salvar**.")
    chosen = {}
    for s in setores:
        current = perm_map.get(s["id"], "nenhum")
        idx = PERMS.index(current) if current in PERMS else 0
        chosen[s["id"]] = st.selectbox(
            s["nome"], PERMS, index=idx, key=f"perm_{target_user_id}_{s['id']}")

    colA, colB = st.columns([0.5, 0.5])
    with colA:
        if st.button(
            "Salvar permissões",
            icon=":material/save:",
            type="primary",
                use_container_width=True):
            try:
                svc.table("user_setores").delete().eq(
                    "tenant_id", tenant_id).eq(
                    "user_id", target_user_id).execute()
                payload = [
                    {"tenant_id": tenant_id, "user_id": target_user_id, "setor_id": sid, "permissao": p}
                    for sid, p in chosen.items() if p in ("view", "edit")
                ]
                if payload:
                    svc.table("user_setores").insert(payload).execute()
                st.success("Permissões salvas.")
                rerun_fn()
            except Exception as e:
                st.error("Erro ao salvar permissões.")
                st.json(safe_json_fn(e))

    with colB:
        if st.button("Limpar permissões", use_container_width=True):
            try:
                svc.table("user_setores").delete().eq(
                    "tenant_id", tenant_id).eq(
                    "user_id", target_user_id).execute()
                st.success("Permissões removidas.")
                rerun_fn()
            except Exception as e:
                st.error("Erro ao limpar permissões.")
                st.json(safe_json_fn(e))
