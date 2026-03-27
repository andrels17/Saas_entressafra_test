from src.ui.components.filters import select_revisao
from src.ui.components.forms import (
    form_section,
    form_submit_button,
    validate_required,
    validate_date_range,
    validation_summary,
)
from src.ui.components.confirmations import confirmation_panel
from src.ui.core.styles import page_header as _ph
import math
import streamlit as st
from postgrest.exceptions import APIError

from src.utils.supabase_helpers import sb_for_user, current_tenant_id, current_role
from src.db.supabase_client import get_supabase_service, get_supabase_anon
from src.utils import nav
from src.services.dashboard_cache_service import refresh_dashboard_cache
from src.ui.core.cache import bump_data_version


STATUSES = ["pendente", "em_andamento", "concluido", "travado", "nao_aplica"]


def _hidden_deleted_revisoes() -> set[str]:
    raw = st.session_state.get("_hidden_deleted_revisoes")
    if isinstance(raw, set):
        return {str(x) for x in raw}
    if isinstance(raw, (list, tuple)):
        return {str(x) for x in raw}
    return set()


def _mark_revisao_deleted_ui(revisao_id: str) -> None:
    if not revisao_id:
        return
    hidden = _hidden_deleted_revisoes()
    hidden.add(str(revisao_id))
    st.session_state["_hidden_deleted_revisoes"] = hidden


def _unmark_revisao_deleted_ui(revisao_id: str) -> None:
    if not revisao_id:
        return
    hidden = _hidden_deleted_revisoes()
    hidden.discard(str(revisao_id))
    st.session_state["_hidden_deleted_revisoes"] = hidden


def _clear_revision_ui_caches() -> None:
    try:
        st.cache_data.clear()
    except Exception:
        pass
    try:
        st.cache_resource.clear()
    except Exception:
        pass



@st.cache_data(ttl=30, show_spinner=False)
def _fetch_revisoes_cached(tenant_id: str, _token: str = "") -> list[dict]:
    """Carrega revisões com cache (ttl=30s). Usa service role como fallback."""
    sb = get_supabase_anon()
    if _token:
        sb.postgrest.auth(_token)
    try:
        return (
            sb.table("revisoes")
            .select("id,titulo,status,data_inicio,data_fim,semanas_total,created_at")
            .eq("tenant_id", tenant_id)
            .order("created_at", desc=True)
            .execute()
            .data
        ) or []
    except APIError:
        try:
            svc = get_supabase_service()
            return (
                svc.table("revisoes")
                .select("id,titulo,status,data_inicio,data_fim,semanas_total,created_at")
                .eq("tenant_id", tenant_id)
                .order("created_at", desc=True)
                .execute()
                .data
            ) or []
        except Exception:
            return []


@st.cache_data(ttl=30, show_spinner=False)
def _fetch_revisoes_min_cached(tenant_id: str, _token: str = "") -> list[dict]:
    """Versão mínima (menos campos) com cache para seleção de revisão."""
    sb = get_supabase_anon()
    if _token:
        sb.postgrest.auth(_token)
    try:
        return (
            sb.table("revisoes")
            .select("id,titulo,status,semanas_total,created_at")
            .eq("tenant_id", tenant_id)
            .order("created_at", desc=True)
            .execute()
            .data
        ) or []
    except APIError:
        try:
            svc = get_supabase_service()
            return (
                svc.table("revisoes")
                .select("id,titulo,status,semanas_total,created_at")
                .eq("tenant_id", tenant_id)
                .order("created_at", desc=True)
                .execute()
                .data
            ) or []
        except Exception:
            return []


@st.cache_data(ttl=30, show_spinner=False)
def _load_grupos_cached(tenant_id: str, _token: str = "") -> list[dict]:
    """Grupos ativos para seleção na sincronização (cacheado)."""
    sb = get_supabase_anon()
    if _token:
        sb.postgrest.auth(_token)
    try:
        return (
            sb.table("equip_grupos")
            .select("id,nome,ativo,departamento_id")
            .eq("tenant_id", tenant_id)
            .eq("ativo", True)
            .order("nome")
            .execute()
            .data
        ) or []
    except Exception:
        return []


def _fetch_revisoes(sb, tenant_id):
    """Compat: usa token do session_state se disponível."""
    import streamlit as _st
    token = _st.session_state.get("sb_access_token", "")
    return _fetch_revisoes_cached(tenant_id, token)


def _fetch_revisoes_min(sb, tenant_id):
    """Compat: usa token do session_state se disponível."""
    import streamlit as _st
    token = _st.session_state.get("sb_access_token", "")
    return _fetch_revisoes_min_cached(tenant_id, token)


def _chunk(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def _load_grupos(sb, tenant_id):
    """Compat: delega para versão cacheada."""
    import streamlit as _st
    token = _st.session_state.get("sb_access_token", "")
    return _load_grupos_cached(tenant_id, token)


def _load_equipamentos(sb, tenant_id):
    return (
        sb.table("equipamentos")
        .select("id,grupo_id")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .execute()
        .data
    ) or []




def _norm_key(v):
    return str(v) if v is not None else None


def _dedup_equipamentos(rows):
    seen = set()
    out = []
    for row in rows or []:
        eid = _norm_key(row.get("id"))
        if not eid or eid in seen:
            continue
        seen.add(eid)
        item = dict(row)
        item["id"] = eid
        item["grupo_id"] = _norm_key(row.get("grupo_id"))
        out.append(item)
    return out


def _dedup_task_payload(payload):
    seen = set()
    out = []
    for row in payload or []:
        item = dict(row)
        item["tenant_id"] = _norm_key(item.get("tenant_id"))
        item["revisao_id"] = _norm_key(item.get("revisao_id"))
        item["equipamento_id"] = _norm_key(item.get("equipamento_id"))
        item["servico_id"] = _norm_key(item.get("servico_id"))
        key = (
            item.get("tenant_id"),
            item.get("revisao_id"),
            item.get("equipamento_id"),
            item.get("servico_id"),
        )
        if None in key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out

def _load_grupo_servicos(sb, tenant_id, grupo_ids):
    if not grupo_ids:
        return {}
    rows = (
        sb.table("grupo_servicos")
        .select("grupo_id,servico_id")
        .eq("tenant_id", tenant_id)
        .in_("grupo_id", list(grupo_ids))
        .execute()
        .data
    ) or []
    gs = {}
    for r in rows:
        gs.setdefault(r["grupo_id"], set()).add(r["servico_id"])
    return gs


def _load_existing_tasks(sb, tenant_id, revisao_id, equipamento_ids):
    existing = {}
    equipamento_ids = [_norm_key(i) for i in (equipamento_ids or []) if i is not None]
    for ids in _chunk(equipamento_ids, 200):
        rows = (
            sb.table("tarefas_servico")
            .select("id,equipamento_id,servico_id,status")
            .eq("tenant_id", tenant_id)
            .eq("revisao_id", revisao_id)
            .in_("equipamento_id", ids)
            .execute()
            .data
        ) or []
        for r in rows:
            eid = _norm_key(r.get("equipamento_id"))
            sid = _norm_key(r.get("servico_id"))
            if not eid or not sid:
                continue
            item = dict(r)
            item["equipamento_id"] = eid
            item["servico_id"] = sid
            existing.setdefault(eid, {})[sid] = item
    return existing


def _insert_tasks(sb, payload):
    if not payload:
        return 0

    dedup = _dedup_task_payload(payload)

    for batch in _chunk(dedup, 500):
        sb.table("tarefas_servico").upsert(
            batch,
            on_conflict="tenant_id,revisao_id,equipamento_id,servico_id",
            ignore_duplicates=True,
        ).execute()

    return len(dedup)


def _update_tasks_status(sb, ids, status="nao_aplica"):
    for batch in _chunk(ids, 500):
        sb.table("tarefas_servico").update(
            {"status": status}).in_("id", batch).execute()


def _safe_count_rows(
        client,
        table_name: str,
        tenant_id: str,
        revisao_id: str) -> int:
    try:
        resp = (
            client.table(table_name)
            .select("id", count="exact")
            .eq("tenant_id", tenant_id)
            .eq("revisao_id", revisao_id)
            .execute()
        )
        return int(getattr(resp, "count", 0) or 0)
    except Exception:
        try:
            rows = (
                client.table(table_name)
                .select("id")
                .eq("tenant_id", tenant_id)
                .eq("revisao_id", revisao_id)
                .limit(10_000)  # usar fetch_all() se exceder
                .execute()
                .data
            ) or []
            return len(rows)
        except Exception:
            return 0


def _delete_revisao_cascade(tenant_id: str, revisao_id: str) -> dict:
    svc = get_supabase_service()
    result = {"historico": 0, "tarefas": 0, "revisoes": 0}

    result["historico"] = _safe_count_rows(
        svc, "historico_eventos", tenant_id, revisao_id)
    result["tarefas"] = _safe_count_rows(
        svc, "tarefas_servico", tenant_id, revisao_id)

    # Ordem importante: histórico -> tarefas -> revisão
    try:
        svc.table("historico_eventos").delete().eq(
            "tenant_id", tenant_id).eq(
            "revisao_id", revisao_id).execute()
    except Exception as _e:
        import logging; logging.getLogger("saas").warning("revisoes.py: %s", _e)

    svc.table("tarefas_servico").delete().eq(
        "tenant_id", tenant_id).eq(
        "revisao_id", revisao_id).execute()
    svc.table("revisoes").delete().eq(
        "tenant_id", tenant_id).eq(
        "id", revisao_id).execute()
    result["revisoes"] = 1
    return result


def _safe_distinct_task_summary(tenant_id: str, revisao_id: str) -> dict:
    svc = get_supabase_service()
    out = {
        "equipamentos": 0,
        "tarefas_concluidas": 0,
        "tarefas_pendentes": 0,
        "tarefas_total": 0,
        "historico": 0,
    }
    try:
        rows = (
            svc.table("tarefas_servico")
            .select("equipamento_id,status")
            .eq("tenant_id", tenant_id)
            .eq("revisao_id", revisao_id)
            .limit(20_000)  # usar fetch_all() se exceder
            .execute()
            .data
        ) or []
        equipamentos = {r.get("equipamento_id")
                        for r in rows if r.get("equipamento_id") is not None}
        concl = sum(1 for r in rows if r.get("status") == "concluido")
        pend = sum(
            1 for r in rows if r.get("status") in (
                "pendente",
                "em_andamento",
                "travado"))
        out.update({
            "equipamentos": len(equipamentos),
            "tarefas_concluidas": concl,
            "tarefas_pendentes": pend,
            "tarefas_total": len(rows),
        })
    except Exception as _e:
        import logging; logging.getLogger("saas").warning("revisoes.py: %s", _e)

    out["historico"] = _safe_count_rows(
        svc, "historico_eventos", tenant_id, revisao_id)
    return out


def _bulk_delete_test_revisions(
        tenant_id: str, revisoes: list[dict]) -> tuple[int, int, int]:
    total_rev = total_tarefas = total_hist = 0
    for r in revisoes:
        res = _delete_revisao_cascade(tenant_id, r["id"])
        total_rev += int(res.get("revisoes", 0) or 0)
        total_tarefas += int(res.get("tarefas", 0) or 0)
        total_hist += int(res.get("historico", 0) or 0)
    return total_rev, total_tarefas, total_hist


def render_admin_revisoes() -> None:
    _ph("◑", "Revisões", "Gerencie revisões de entressafra e gere/sincronize a Matriz com base nos Templates de Grupo.")

    role = current_role()
    if role not in ("admin", "superadmin"):
        st.error("Apenas Admin pode gerenciar revisões.")
        st.stop()

    tenant_id = current_tenant_id()
    sb = sb_for_user()

    tab1, tab2 = st.tabs(["Revisões", "Gerar / Sincronizar Matriz"])

    with tab1:
        form_section(
            "Criar revisão",
            "Se o seu banco tiver alguma política/trigger recursiva em `revisoes`, o insert pode estourar stack. Para evitar travar o app, a criação aqui usa Service Role (bypassa RLS).",
        )

        titulo = st.text_input(
            "Título",
            placeholder="Entressafra 2026",
            key="rev_titulo")

        modo_calculo = st.radio(
            "Modo de cálculo",
            options=[
                "Por datas",
                "Por semanas"],
            horizontal=True,
            key="rev_modo_calculo",
            help="Escolha se a revisão será definida pelo período ou pela quantidade de semanas.",
        )

        def _calc_weeks(dt_inicio, dt_fim):
            if not dt_inicio or not dt_fim or dt_fim < dt_inicio:
                return 0
            dias = (dt_fim - dt_inicio).days + 1
            return max(1, int(math.ceil(dias / 7)))

        def _calc_end_date(dt_inicio, semanas):
            if not dt_inicio or not semanas or int(semanas) <= 0:
                return None
            from datetime import timedelta
            return dt_inicio + timedelta(days=(int(semanas) * 7) - 1)

        c1, c2, c3 = st.columns(3)
        with c1:
            dt_ini = st.date_input("Data início", value=None, key="rev_dt_ini")

        semanas_input = int(st.session_state.get("rev_semanas_input", 0) or 0)
        if modo_calculo == "Por datas":
            with c2:
                dt_fim = st.date_input(
                    "Data fim", value=None, key="rev_dt_fim")
            semanas_total = _calc_weeks(dt_ini, dt_fim)
            dias_total = (dt_fim - dt_ini).days + \
                1 if dt_ini and dt_fim and dt_fim >= dt_ini else 0
            with c3:
                st.text_input(
                    "Nº semanas",
                    value=str(
                        int(semanas_total)),
                    disabled=True,
                    help="Calculado automaticamente a partir de Data início e Data fim.",
                )
        else:
            with c2:
                st.number_input(
                    "Nº semanas",
                    min_value=1,
                    value=max(
                        1,
                        semanas_input) if semanas_input else 20,
                    step=1,
                    key="rev_semanas_input",
                    help="Aceita qualquer número de semanas. O sistema calculará a data fim automaticamente.",
                )
                at1, at2, at3, at4 = st.columns(4)
                if at1.button("12", key="wk12", use_container_width=True):
                    st.session_state["rev_semanas_input"] = 12
                    st.rerun()
                if at2.button("16", key="wk16", use_container_width=True):
                    st.session_state["rev_semanas_input"] = 16
                    st.rerun()
                if at3.button("20", key="wk20", use_container_width=True):
                    st.session_state["rev_semanas_input"] = 20
                    st.rerun()
                if at4.button("24", key="wk24", use_container_width=True):
                    st.session_state["rev_semanas_input"] = 24
                    st.rerun()
            semanas_total = int(
                st.session_state.get(
                    "rev_semanas_input",
                    20) or 20)
            dt_fim = _calc_end_date(dt_ini, semanas_total)
            dias_total = (dt_fim - dt_ini).days + 1 if dt_ini and dt_fim else 0
            with c3:
                st.text_input(
                    "Data fim",
                    value=dt_fim.strftime("%Y/%m/%d") if dt_fim else "",
                    disabled=True,
                    help="Calculada automaticamente com base na Data início e no Nº semanas.",
                )

        pronto_heatmap = "Sim" if semanas_total > 0 and dt_ini and dt_fim else "Não"

        m1, m2, m3 = st.columns(3)
        m1.metric("Período total", f"{dias_total} dia(s)")
        m2.metric("Semanas geradas", semanas_total)
        m3.metric("Pronto para tendência/heatmap", pronto_heatmap)

        if semanas_total > 0 and dt_ini and dt_fim:
            st.caption("Prévia das semanas da revisão")
            semanas_preview = []
            for idx in range(semanas_total):
                ini_sem = dt_ini + \
                    __import__("datetime").timedelta(days=idx * 7)
                fim_sem = min(
                    ini_sem +
                    __import__("datetime").timedelta(
                        days=6),
                    dt_fim)
                dias_sem = (fim_sem - ini_sem).days + 1
                semanas_preview.append(
                    f"Sem.{
                        idx +
                        1}: {
                        ini_sem.strftime('%d/%m/%Y')} → {
                        fim_sem.strftime('%d/%m/%Y')} ({dias_sem} dia(s))")
            st.markdown(
                "<div style='margin-top:6px'></div>",
                unsafe_allow_html=True)
            for linha in [semanas_preview[i: i + 3]
                          for i in range(0, len(semanas_preview), 3)]:
                cols = st.columns(len(linha))
                for col, texto in zip(cols, linha):
                    with col:
                        st.caption(texto)

        submitted = form_submit_button(
            "Criar revisão",
            key="rev_submit",
            help="Valida os campos e cria a revisão com as semanas calculadas.",
        )

        if submitted:
            t = (titulo or "").strip()
            errors = []
            errors.extend(validate_required({"o título": t}))
            errors.extend(validate_date_range(dt_ini, dt_fim))
            if errors:
                validation_summary(errors, title="Revise os campos da nova revisão")
                st.stop()

            payload = {"tenant_id": tenant_id, "titulo": t, "status": "ativa"}
            payload["data_inicio"] = str(dt_ini)
            payload["data_fim"] = str(dt_fim)
            if semanas_total > 0:
                payload["semanas_total"] = int(semanas_total)

            try:
                svc = get_supabase_service()
                created = (
                    svc.table("revisoes")
                    .insert(payload)
                    .execute()
                    .data
                ) or []
                created_row = created[0] if created else None
                revisao_id = str((created_row or {}).get("id") or "")
                if revisao_id:
                    _unmark_revisao_deleted_ui(revisao_id)
                    refresh_dashboard_cache(svc, tenant_id, revisao_id)
                    bump_data_version()
                    _clear_revision_ui_caches()
                    nav.set_current_revisao(revisao_id, titulo=t)
                st.toast("✓ Revisão criada", icon=":material/check_circle:")
                nav.rerun_keep_menu()
            except Exception as e:
                st.error(f"Erro ao criar revisão: {e}")

        st.divider()
        st.markdown("### Revisões existentes")
        revisoes = _fetch_revisoes(sb, tenant_id)
        hidden_deleted = _hidden_deleted_revisoes()
        if hidden_deleted:
            revisoes = [r for r in revisoes if str(r.get("id")) not in hidden_deleted]

        if revisoes:
            demo_candidates = [
                r for r in revisoes if r.get("status") != "ativa" and any(
                    tok in (
                        r.get("titulo") or "").lower() for tok in (
                        "teste",
                        "demo",
                        "rascunho",
                        "tmp"))]
            with st.expander("Limpeza de demo/testes", expanded=False):
                st.caption(
                    "Remove revisões de teste e todos os dados relacionados (tarefas e histórico). "
                    "Use apenas quando quiser limpar o ambiente de demonstração.")
                st.markdown(
                    f"Candidatas encontradas: **{len(demo_candidates)}**")
                if demo_candidates:
                    st.caption(
                        ", ".join(f"{r.get('titulo')} [{r.get('status')}]" for r in demo_candidates[:8])
                        + (" ..." if len(demo_candidates) > 8 else "")
                    )
                confirm_demo = st.text_input(
                    "Para limpar revisões de teste, digite LIMPAR DEMO",
                    key="bulk_delete_demo_confirm",
                )
                if st.button(
                    "Limpar revisões de teste",
                    type="secondary",
                    use_container_width=True,
                    disabled=not demo_candidates,
                    key="bulk_delete_demo_btn",
                ):
                    st.session_state["confirm_bulk_delete_demo"] = True
                    st.rerun()

                if confirmation_panel(
                    state_key="confirm_bulk_delete_demo",
                    title="Confirma a limpeza das revisões de teste?",
                    body=f"Serão removidas {len(demo_candidates)} revisão(ões) de demo/teste e todos os dados relacionados.",
                    confirm_label="Sim, limpar demo",
                ):
                    if confirm_demo.strip().upper() != "LIMPAR DEMO":
                        st.error(
                            "Confirmação inválida. Digite exatamente LIMPAR DEMO.")
                    else:
                        try:
                            with st.spinner("Limpando revisões de teste..."):
                                n_rev, n_tarefas, n_hist = _bulk_delete_test_revisions(
                                    tenant_id, demo_candidates)
                            bump_data_version()
                            _clear_revision_ui_caches()
                            st.success(
                                f"Limpeza concluída: {n_rev} revisão(ões), {n_tarefas} tarefa(s) e {n_hist} evento(s) removidos.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Erro ao limpar demo: {e}")
        else:
            st.info("Nenhuma revisão criada.")

        if revisoes:
            for r in revisoes:
                with st.container(border=True):
                    rc1, rc2 = st.columns([0.58, 0.42])
                    with rc1:
                        st.markdown(f"**{r['titulo']}**")
                        st.caption(
                            f"Início: {
                                r.get('data_inicio') or '—'} · Fim: {
                                r.get('data_fim') or '—'} · Semanas: {
                                r.get('semanas_total') or '—'}")
                        from src.ui.core.styles import status_badge
                        status_badge(r.get("status"))
                    with rc2:
                        ca, cb, cc, cd = st.columns(4)
                        with ca:
                            if r.get("status") != "ativa":
                                if st.button(
                                    "▶ Ativar",
                                    key=f"rev_active_{r['id']}",
                                    use_container_width=True,
                                    type="secondary",
                                ):
                                    try:
                                        sb.table("revisoes").update({"status": "fechada"}).eq(
                                            "tenant_id", tenant_id).eq("status", "ativa").execute()
                                        sb.table("revisoes").update(
                                            {"status": "ativa"}).eq("id", r["id"]).execute()
                                        st.toast(
                                            "✓ Revisão ativada", icon=":material/check_circle:")
                                        try:
                                            from src.ui.core.cache_matrix import invalidate_matriz_cache
                                            invalidate_matriz_cache()
                                        except Exception:
                                            pass
                                        nav.rerun_keep_menu()
                                    except Exception as e:
                                        st.error(f"Erro: {e}")
                        with cb:
                            if r.get("status") == "ativa":
                                if st.button(
                                    "🔒 Fechar",
                                    key=f"rev_close_wiz_{r['id']}",
                                    use_container_width=True,
                                    type="primary",
                                ):
                                    st.session_state[f"wiz_fechar_{r['id']}"] = True
                        with cc:
                            if st.button(
                                "📦 Arquivar", key=f"rev_arch_{
                                    r['id']}", use_container_width=True):
                                try:
                                    sb.table("revisoes").update(
                                        {"status": "arquivada"}).eq("id", r["id"]).execute()
                                    st.toast(
                                        "✓ Arquivada", icon=":material/check_circle:")
                                    nav.rerun_keep_menu()
                                except Exception as e:
                                    st.error(f"Erro: {e}")
                        with cd:
                            if st.button(
                                "🗑 Excluir", key=f"rev_delete_toggle_{
                                    r['id']}", use_container_width=True):
                                flag = f"wiz_delete_{r['id']}"
                                st.session_state[flag] = not st.session_state.get(
                                    flag, False)

                    delete_flag = f"wiz_delete_{r['id']}"
                    if st.session_state.get(delete_flag):
                        resumo = _safe_distinct_task_summary(
                            tenant_id, r["id"])
                        st.markdown("---")
                        st.error(
                            "Exclusão permanente. Esta ação apaga a revisão e todos os dados vinculados a ela."
                        )
                        st.caption("Impacto estimado da exclusão")
                        d1, d2, d3, d4 = st.columns(4)
                        d1.metric(
                            "Equipamentos impactados", resumo.get(
                                "equipamentos", 0))
                        d2.metric(
                            "Tarefas concluídas", resumo.get(
                                "tarefas_concluidas", 0))
                        d3.metric(
                            "Tarefas pendentes", resumo.get(
                                "tarefas_pendentes", 0))
                        d4.metric(
                            "Histórico gerado", resumo.get(
                                "historico", 0))
                        x1, x2 = st.columns(2)
                        x1.metric(
                            "Tarefas totais", resumo.get(
                                "tarefas_total", 0))
                        x2.metric("Revisão", 1)
                        st.caption(
                            f"Para confirmar a exclusão de **{r['titulo']}**, digite exatamente: EXCLUIR {r['titulo']}"
                        )
                        confirm = st.text_input(
                            "Confirmação de exclusão",
                            key=f"delete_confirm_input_{r['id']}",
                            placeholder=f"EXCLUIR {r['titulo']}",
                        )
                        dx1, dx2 = st.columns(2)
                        with dx1:
                            if st.button(
                                "Confirmar exclusão permanente",
                                key=f"delete_confirm_btn_{r['id']}",
                                type="primary",
                                use_container_width=True,
                            ):
                                expected = f"EXCLUIR {r['titulo']}"
                                if (confirm or "").strip() != expected:
                                    st.error(
                                        "Confirmação inválida. Copie o texto exatamente como mostrado.")
                                else:
                                    try:
                                        with st.spinner("Excluindo revisão e dados vinculados..."):
                                            res = _delete_revisao_cascade(
                                                tenant_id, r["id"])
                                        st.session_state.pop(delete_flag, None)
                                        st.session_state.pop(
                                            f"delete_confirm_input_{r['id']}", None)
                                        _mark_revisao_deleted_ui(r["id"])
                                        if str(nav.get_current_revisao() or "") == str(r["id"]):
                                            nav.set_current_revisao(None, titulo=None)
                                        bump_data_version()
                                        _clear_revision_ui_caches()
                                        st.success(
                                            f"Revisão excluída. Removidos: {res.get('tarefas', 0)} tarefa(s), {res.get('historico', 0)} evento(s) e 1 revisão.")
                                        st.rerun()
                                    except Exception as e:
                                        st.error(
                                            f"Erro ao excluir revisão: {e}")
                        with dx2:
                            if st.button(
                                "Cancelar exclusão", key=f"delete_cancel_btn_{
                                    r['id']}", use_container_width=True):
                                st.session_state.pop(delete_flag, None)
                                st.session_state.pop(
                                    f"delete_confirm_input_{r['id']}", None)
                                st.rerun()

                    # Wizard de fechamento
                    if st.session_state.get(f"wiz_fechar_{r['id']}"):
                        st.markdown("---")
                        st.markdown("#### 🔒 Checklist de fechamento")
                        st.caption(
                            "Verifique os itens abaixo antes de encerrar a revisão.")

                        with st.spinner("Carregando diagnóstico…"):
                            try:
                                tarefas_rev = (
                                    sb.table("tarefas_servico")
                                    .select("id,status,etapa_d,etapa_r,etapa_m,equipamentos(grupo_id)")
                                    .eq("tenant_id", tenant_id)
                                    .eq("revisao_id", r["id"])
                                    .execute()
                                    .data
                                ) or []
                            except Exception:
                                tarefas_rev = []

                        total_t = len(tarefas_rev)
                        concluidos = sum(
                            1 for t in tarefas_rev if t.get("status") == "concluido")
                        travados = sum(
                            1 for t in tarefas_rev if t.get("status") == "travado")
                        pendentes = sum(
                            1 for t in tarefas_rev if t.get("status") in (
                                "pendente", "em_andamento"))
                        pct_geral = round((concluidos / max(total_t, 1)) * 100)

                        wc1, wc2, wc3, wc4 = st.columns(4)
                        wc1.metric("Total tarefas", total_t)
                        wc2.metric(
                            "Concluídas",
                            concluidos,
                            delta=f"{pct_geral}%")
                        wc3.metric(
                            "Pendentes/And.",
                            pendentes,
                            delta_color="inverse" if pendentes else "off")
                        wc4.metric(
                            "Travadas",
                            travados,
                            delta_color="inverse" if travados else "off")

                        chk1 = pct_geral >= 80
                        chk2 = travados == 0
                        chk3 = pendentes <= (total_t * 0.1)

                        def _chk_icon(ok):
                            return "✅" if ok else "⚠️"

                        st.markdown(
                            f"{_chk_icon(chk1)} Progresso global ≥ 80% — **{pct_geral}%**\n\n"
                            f"{_chk_icon(chk2)} Nenhum item travado — **{travados} travado(s)**\n\n"
                            f"{_chk_icon(chk3)} Pendentes ≤ 10% do total — **{pendentes} pendente(s)**"
                        )

                        tem_bloqueio = not chk1 or not chk2
                        if tem_bloqueio:
                            st.warning(
                                "⚠️ Existem itens críticos em aberto. "
                                "Recomendamos resolver antes de fechar, mas você pode forçar o fechamento se necessário.")

                        col_conf, _, col_cancel = st.columns([1, 1, 1])
                        with col_conf:
                            _lbl_conf = "✅ Confirmar fechamento" if not tem_bloqueio else "✅ Fechar mesmo assim"
                            if st.button(
                                    _lbl_conf,
                                    key=f"wiz_conf_{
                                        r['id']}",
                                    type="primary",
                                    use_container_width=True):
                                try:
                                    sb.table("revisoes").update(
                                        {"status": "fechada"}).eq("id", r["id"]).execute()
                                    st.session_state.pop(
                                        f"wiz_fechar_{r['id']}", None)
                                    st.toast(
                                        "🔒 Revisão encerrada.", icon=":material/lock:")
                                    try:
                                        from src.ui.core.cache_matrix import invalidate_matriz_cache
                                        invalidate_matriz_cache()
                                    except Exception:
                                        pass
                                    nav.rerun_keep_menu()
                                except Exception as e:
                                    st.error(f"Erro ao fechar: {e}")
                        with col_cancel:
                            if st.button(
                                "✖ Cancelar", key=f"wiz_cancel_{
                                    r['id']}", use_container_width=True):
                                st.session_state.pop(
                                    f"wiz_fechar_{r['id']}", None)
                                st.rerun()

                    st.markdown(
                        "<div style='height:4px'></div>",
                        unsafe_allow_html=True)

    with tab2:
        st.markdown("### Selecionar revisão")
        revisoes = _fetch_revisoes_min(sb, tenant_id)

        if not revisoes:
            st.info("Crie uma revisão primeiro.")
            return

        revisao = select_revisao(
            revisoes,
            key="revisoes_select",
            show_status_icon=False,
        )
        if not revisao:
            st.info("Crie uma revisão primeiro.")
            return
        revisao_id = revisao["id"]

        st.divider()
        st.markdown("### Pré-checagens")
        grupos = _load_grupos(sb, tenant_id)
        if not grupos:
            st.warning("Você precisa criar grupos.")
            st.stop()

        eqs = _dedup_equipamentos(_load_equipamentos(sb, tenant_id))
        total_eq = len(eqs)
        sem_grupo = [e for e in eqs if not e.get("grupo_id")]
        com_grupo = [e for e in eqs if e.get("grupo_id")]

        st.markdown(f"- Equipamentos ativos: **{total_eq:,}**")
        st.markdown(f"- Com grupo: **{len(com_grupo):,}**")
        st.markdown(
            f"- Sem grupo: **{len(sem_grupo):,}** (não entram na matriz)")

        grupos_ids = {e["grupo_id"] for e in com_grupo if e.get("grupo_id")}
        gs_map = _load_grupo_servicos(sb, tenant_id, grupos_ids)

        grupos_sem_template = [
            gid for gid in grupos_ids if gid not in gs_map or not gs_map.get(gid)]
        if grupos_sem_template:
            st.warning(
                "Há grupos sem template de serviços. Eles gerarão 0 tarefas até você configurar o Template.")
        else:
            st.toast("✓ Templates verificados", icon=":material/check_circle:")

        st.divider()
        st.markdown("### Ações")

        colA, colB = st.columns(2)

        with colA:
            if st.button(
                "Gerar Matriz (inserir faltantes)",
                icon=":material/grid_on:",
                type="primary",
                    use_container_width=True):
                st.session_state["confirm_generate_matrix"] = True
                st.rerun()

            if len(com_grupo) == 0:
                payload = []
            else:
                equipamento_ids = [e["id"] for e in com_grupo]
                existing = _load_existing_tasks(
                    sb, tenant_id, revisao_id, equipamento_ids)

                payload = []
                for e in com_grupo:
                    gid = e["grupo_id"]
                    servs = gs_map.get(gid, set())
                    if not servs:
                        continue
                    cur = existing.get(e["id"], {})
                    for sid in servs:
                        if sid in cur:
                            continue
                        payload.append({
                            "tenant_id": tenant_id,
                            "revisao_id": revisao_id,
                            "equipamento_id": e["id"],
                            "servico_id": sid,
                            "status": "pendente",
                            "etapa_d": False,
                            "etapa_r": False,
                            "etapa_m": False,
                        })

            payload = _dedup_task_payload(payload)
            st.info(f"Tarefas novas a inserir: **{len(payload):,}**")
            if confirmation_panel(
                state_key="confirm_generate_matrix",
                title="Confirmar geração da matriz?",
                body=f"Serão inseridas {len(payload):,} tarefa(s) faltante(s) com base nos templates atuais do grupo.",
                confirm_label="Gerar agora",
            ):
                if len(com_grupo) == 0:
                    st.warning("Nenhum equipamento com grupo.")
                    st.stop()
                if payload:
                    try:
                        with st.spinner("Inserindo tarefas..."):
                            inserted_count = _insert_tasks(sb, payload)
                        # Invalida caches para que Dashboard e Home
                        # reflitam os dados novos imediatamente.
                        try:
                            from src.ui.core.cache_matrix import invalidate_matriz_cache
                            invalidate_matriz_cache()
                        except Exception:
                            pass
                        st.toast(
                            f"✓ Matriz gerada/atualizada · inseridas {inserted_count:,} tarefa(s)",
                            icon=":material/check_circle:")
                        nav.rerun_keep_menu()
                    except Exception as e:
                        st.error(f"Erro ao inserir tarefas: {e}")
                else:
                    st.info(
                        "Nada a inserir: matriz já estava completa para os templates atuais.")

        with colB:
            if st.button(
                "Sincronizar Matriz (add + marcar N/A)",
                    use_container_width=True):
                st.session_state["confirm_sync_matrix"] = True
                st.rerun()

            if len(com_grupo) == 0:
                to_insert = []
                to_na = []
            else:
                equipamento_ids = [e["id"] for e in com_grupo]
                existing = _load_existing_tasks(
                    sb, tenant_id, revisao_id, equipamento_ids)

                to_insert = []
                to_na = []

                for e in com_grupo:
                    gid = e["grupo_id"]
                    desired = gs_map.get(gid, set()) or set()
                    cur_map = existing.get(e["id"], {})
                    cur_servicos = set(cur_map.keys())

                    for sid in (desired - cur_servicos):
                        to_insert.append({
                            "tenant_id": tenant_id,
                            "revisao_id": revisao_id,
                            "equipamento_id": e["id"],
                            "servico_id": sid,
                            "status": "pendente",
                            "etapa_d": False,
                            "etapa_r": False,
                            "etapa_m": False,
                        })

                    for sid in (cur_servicos - desired):
                        row = cur_map.get(sid)
                        if row and row.get("status") != "nao_aplica":
                            to_na.append(row["id"])

            to_insert = _dedup_task_payload(to_insert)
            st.markdown(f"- Inserir: **{len(to_insert):,}**")
            st.markdown(f"- Marcar como não aplica: **{len(to_na):,}**")

            if confirmation_panel(
                state_key="confirm_sync_matrix",
                title="Confirmar sincronização da matriz?",
                body=f"Serão inseridas {len(to_insert):,} tarefa(s) e {len(to_na):,} tarefa(s) poderão ser marcadas como Não aplica.",
                confirm_label="Sincronizar agora",
            ):
                if len(com_grupo) == 0:
                    st.warning("Nenhum equipamento com grupo.")
                    st.stop()

                try:
                    with st.spinner("Sincronizando..."):
                        inserted_count = 0
                        if to_insert:
                            inserted_count = _insert_tasks(sb, to_insert)
                        if to_na:
                            _update_tasks_status(
                                sb, to_na, status="nao_aplica")
                    # Invalida todos os caches após sincronização para garantir
                    # que Dashboard e Home reflitam os novos dados imediatamente.
                    try:
                        from src.ui.core.cache_matrix import invalidate_matriz_cache
                        invalidate_matriz_cache()
                    except Exception:
                        pass
                    st.toast(
                        f"✓ Sincronização concluída · inseridas {inserted_count:,} tarefa(s)",
                        icon=":material/check_circle:")
                    nav.rerun_keep_menu()
                except Exception as e:
                    st.error(f"Erro na sincronização: {e}")
