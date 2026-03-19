import streamlit as st
import pandas as pd

from src.utils.supabase_helpers import sb_for_user, current_tenant_id, current_role
from src.ui.core.styles import page_header as _ph


def _confirm_phrase(key: str, phrase: str) -> bool:
    txt = st.text_input(f"Digite {phrase} para confirmar", key=key, value="")
    return txt.strip().upper() == phrase.upper()


def _safe_select_equipamentos(sb, tenant_id: str):
    """Busca equipamentos com fallback caso algumas colunas não existam na base."""
    try:
        return (sb.table("equipamentos")
                .select("id,frota,modelo,ativo,grupo_id,departamento_id")
                .eq("tenant_id", tenant_id)
                .execute().data) or []
    except Exception:
        try:
            return (sb.table("equipamentos")
                    .select("id,frota,ativo,grupo_id,departamento_id")
                    .eq("tenant_id", tenant_id)
                    .execute().data) or []
        except Exception:
            return (sb.table("equipamentos")
                    .select("id,ativo,grupo_id,departamento_id")
                    .eq("tenant_id", tenant_id)
                    .execute().data) or []


def render_admin_integridade() -> None:
    _ph("🧪", "Integridade",
        "Diagnóstico e correção rápida de inconsistências (órfãos, vazios e campos faltando).")

    role = current_role()
    if role not in ("admin", "superadmin"):
        st.error("Apenas Admin/Superadmin pode acessar.")
        st.stop()

    tenant_id = current_tenant_id()
    sb = sb_for_user()

    # ---- Fetch (best-effort; tabelas podem variar entre bases) ----
    try:
        deps = (sb.table("departamentos").select("id,nome,ativo").eq(
            "tenant_id", tenant_id).execute().data) or []
    except Exception as e:
        st.error(f"Falha ao ler departamentos (RLS/colunas): {e}")
        deps = []

    try:
        grps = (
            sb.table("equip_grupos").select("id,nome,ativo,departamento_id").eq(
                "tenant_id",
                tenant_id).execute().data) or []
    except Exception as e:
        st.error(f"Falha ao ler grupos (RLS/colunas): {e}")
        grps = []

    try:
        eqs = _safe_select_equipamentos(sb, tenant_id)
    except Exception as e:
        st.error(f"Falha ao ler equipamentos (RLS/colunas): {e}")
        eqs = []

    dep_ids = {d.get("id") for d in deps if d.get("id")}

    # ---- Anomalias ----
    equipamentos_sem_grupo = [
        e for e in eqs if e.get(
            "ativo",
            True) and not e.get("grupo_id")]
    equipamentos_sem_dep = [e for e in eqs if e.get(
        "ativo", True) and not e.get("departamento_id")]
    grupos_sem_dep = [
        g for g in grps if g.get(
            "ativo", True) and (
            g.get("departamento_id") is None or g.get("departamento_id") not in dep_ids)]

    # contagens
    grp_to_eq_cnt: dict[str, int] = {}
    for e in eqs:
        gid = e.get("grupo_id")
        if gid:
            grp_to_eq_cnt[gid] = grp_to_eq_cnt.get(gid, 0) + 1
    dep_to_grp_cnt: dict[str, int] = {}
    for g in grps:
        did = g.get("departamento_id")
        if did:
            dep_to_grp_cnt[did] = dep_to_grp_cnt.get(did, 0) + 1

    grupos_vazios = [
        g for g in grps if g.get(
            "ativo",
            True) and grp_to_eq_cnt.get(
            g.get("id"),
            0) == 0]
    deps_vazios = [
        d for d in deps if d.get(
            "ativo",
            True) and dep_to_grp_cnt.get(
            d.get("id"),
            0) == 0]

    k1, k2, k3, k4 = st.columns(4, gap="small")
    k1.metric("Equip. sem Grupo", len(equipamentos_sem_grupo))
    k2.metric("Equip. sem Depto", len(equipamentos_sem_dep))
    k3.metric("Grupos sem Depto", len(grupos_sem_dep))
    k4.metric("Grupos vazios", len(grupos_vazios))

    st.caption("Esta tela é focada em **integridade**. Para deduplicação/limpeza pesada, use as abas de Limpeza em cada módulo.")

    # Link rápido para o Supabase Studio (abre em nova aba)
    _supabase_url = st.secrets.get("SUPABASE_URL", "")
    if _supabase_url:
        _studio_url = _supabase_url.replace(
            ".supabase.co",
            ".supabase.co/project/_/editor") if "supabase.co" in _supabase_url else _supabase_url
        st.link_button(
            "Abrir Supabase Studio",
            url=_studio_url,
            icon=":material/open_in_new:",
            help="Abre o editor SQL do Supabase em nova aba para correções avançadas",
        )

    tab_diag, tab_fix = st.tabs(["📋 Diagnóstico", "🛠️ Correções rápidas"])

    with tab_diag:
        c1, c2 = st.columns(2, gap="large")
        with c1:
            st.subheader("Equipamentos sem Grupo")
            if equipamentos_sem_grupo:
                df = pd.DataFrame([{"id": e.get("id"), "frota": e.get(
                    "frota"), "modelo": e.get("modelo")} for e in equipamentos_sem_grupo[:500]])
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.info("Nenhum encontrado.")
        with c2:
            st.subheader("Grupos sem Departamento")
            if grupos_sem_dep:
                df = pd.DataFrame([{"id": g.get("id"), "nome": g.get(
                    "nome"), "departamento_id": g.get("departamento_id")} for g in grupos_sem_dep[:500]])
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.info("Nenhum encontrado.")

        st.divider()
        c3, c4 = st.columns(2, gap="large")
        with c3:
            st.subheader("Departamentos vazios (sem grupos)")
            if deps_vazios:
                df = pd.DataFrame(
                    [{"id": d.get("id"), "nome": d.get("nome")} for d in deps_vazios[:500]])
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.info("Nenhum encontrado.")
        with c4:
            st.subheader("Grupos vazios (sem equipamentos)")
            if grupos_vazios:
                df = pd.DataFrame([{"id": g.get("id"), "nome": g.get(
                    "nome"), "departamento_id": g.get("departamento_id")} for g in grupos_vazios[:500]])
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.info("Nenhum encontrado.")

        st.divider()
        st.subheader("Equipamentos sem Departamento")
        if equipamentos_sem_dep:
            df = pd.DataFrame([{"id": e.get("id"), "frota": e.get(
                "frota"), "modelo": e.get("modelo")} for e in equipamentos_sem_dep[:500]])
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("Nenhum encontrado.")

    with tab_fix:
        st.subheader("1) Desativar (soft) órfãos/vazios")
        st.caption("Ações seguras: apenas marca **ativo=false**.")

        b1, b2, b3 = st.columns(3, gap="small")
        with b1:
            if st.button(
                "Desativar grupos vazios",
                icon=":material/block:",
                use_container_width=True,
                disabled=(
                    len(grupos_vazios) == 0)):
                with st.status("Desativando grupos vazios…", expanded=False):
                    ids = [g.get("id") for g in grupos_vazios if g.get("id")]
                    for i in range(0, len(ids), 200):
                        sb.table("equip_grupos").update({"ativo": False}).eq(
                            "tenant_id", tenant_id).in_("id", ids[i:i + 200]).execute()
                st.toast("Grupos vazios desativados.", icon=":material/block:")
                st.rerun()

        with b2:
            if st.button(
                "Desativar departamentos vazios",
                icon=":material/block:",
                use_container_width=True,
                disabled=(
                    len(deps_vazios) == 0)):
                with st.status("Desativando departamentos vazios…", expanded=False):
                    ids = [d.get("id") for d in deps_vazios if d.get("id")]
                    for i in range(0, len(ids), 200):
                        sb.table("departamentos").update({"ativo": False}).eq(
                            "tenant_id", tenant_id).in_("id", ids[i:i + 200]).execute()
                st.toast(
                    "Departamentos vazios desativados.",
                    icon=":material/block:")
                st.rerun()

        with b3:
            if st.button(
                "Desativar equipamentos sem grupo",
                icon=":material/block:",
                use_container_width=True,
                disabled=(
                    len(equipamentos_sem_grupo) == 0)):
                with st.status("Desativando equipamentos sem grupo…", expanded=False):
                    ids = [e.get("id")
                           for e in equipamentos_sem_grupo if e.get("id")]
                    for i in range(0, len(ids), 200):
                        sb.table("equipamentos").update({"ativo": False}).eq(
                            "tenant_id", tenant_id).in_("id", ids[i:i + 200]).execute()
                st.toast(
                    "Equipamentos sem grupo desativados.",
                    icon=":material/block:")
                st.rerun()

        st.divider()
        st.subheader("2) Corrigir grupos sem departamento")
        st.caption("Move todos os grupos órfãos para um departamento escolhido.")

        if not deps:
            st.warning(
                "Nenhum departamento encontrado. Crie um departamento antes.")
            return

        dep_labels = [
            f"{d.get('nome') or 'Sem nome'} ({str(d.get('id'))[:8]})" for d in deps]
        dep_choice = st.selectbox("Departamento destino", dep_labels, index=0)
        dest_dep_id = deps[dep_labels.index(dep_choice)].get("id")

        ok = _confirm_phrase("fix_grupos_dep_phrase", "MOVER")
        if st.button(
            "Mover grupos órfãos para este departamento",
            type="primary",
            use_container_width=True,
            disabled=(not grupos_sem_dep or not ok or not dest_dep_id),
        ):
            with st.status("Atualizando grupos…", expanded=False):
                ids = [g.get("id") for g in grupos_sem_dep if g.get("id")]
                for i in range(0, len(ids), 200):
                    sb.table("equip_grupos").update({"departamento_id": dest_dep_id}).eq(
                        "tenant_id", tenant_id).in_("id", ids[i:i + 200]).execute()
            st.toast("Grupos atualizados.", icon=":material/check:")
            st.rerun()
