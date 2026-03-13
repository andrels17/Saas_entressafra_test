import math
import time
from datetime import date, timedelta

import streamlit as st
from postgrest.exceptions import APIError

from src.utils.supabase_helpers import sb_for_user, current_tenant_id, current_role
from src.db.supabase_client import get_supabase_service
from src.utils import nav


STATUSES = ["pendente", "em_andamento", "concluido", "travado", "nao_aplica"]


def _fetch_revisoes(sb, tenant_id):
    """Fetch revisoes; if RLS/policy blocks, fallback to service role."""
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


def _fetch_revisoes_min(sb, tenant_id):
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


def _chunk(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def _calc_weeks(dt_ini: date | None, dt_fim: date | None) -> int:
    if not dt_ini or not dt_fim or dt_fim < dt_ini:
        return 0
    dias_total = (dt_fim - dt_ini).days + 1
    return int(math.ceil(dias_total / 7))


def _build_week_preview(dt_ini: date | None, dt_fim: date | None):
    if not dt_ini or not dt_fim or dt_fim < dt_ini:
        return []
    semanas = []
    atual = dt_ini
    idx = 1
    while atual <= dt_fim:
        fim_semana = min(atual + timedelta(days=6), dt_fim)
        semanas.append(
            {
                "Semana": f"Sem.{idx}",
                "Início": atual.strftime("%d/%m/%Y"),
                "Fim": fim_semana.strftime("%d/%m/%Y"),
                "Dias": (fim_semana - atual).days + 1,
            }
        )
        atual = fim_semana + timedelta(days=1)
        idx += 1
    return semanas


def _load_grupos(sb, tenant_id):
    return (
        sb.table("equip_grupos")
        .select("id,nome")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .order("nome")
        .execute()
        .data
    ) or []


def _load_equipamentos(sb, tenant_id):
    return (
        sb.table("equipamentos")
        .select("id,grupo_id")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .execute()
        .data
    ) or []


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
            existing.setdefault(r["equipamento_id"], {})[r["servico_id"]] = r
    return existing


def _insert_tasks(sb, payload):
    for batch in _chunk(payload, 500):
        sb.table("tarefas_servico").insert(batch).execute()


def _update_tasks_status(sb, ids, status="nao_aplica"):
    for batch in _chunk(ids, 500):
        sb.table("tarefas_servico").update({"status": status}).in_("id", batch).execute()


from src.ui.core.styles import page_header as _ph


def render_admin_revisoes():
    _ph("◑", "Revisões", "Gerencie revisões de entressafra e gere/sincronize a Matriz com base nos Templates de Grupo.")

    role = current_role()
    if role not in ("admin", "superadmin"):
        st.error("Apenas Admin pode gerenciar revisões.")
        st.stop()

    tenant_id = current_tenant_id()
    sb = sb_for_user()

    tab1, tab2 = st.tabs(["Revisões", "Gerar / Sincronizar Matriz"])

    with tab1:
        st.markdown("### Criar revisão")
        st.info(
            "Se o seu banco tiver alguma política/trigger recursiva em `revisoes`, o insert pode estourar stack. "
            "Para evitar travar o app, a criação aqui usa **Service Role** (bypassa RLS)."
        )

        titulo = st.text_input("Título", placeholder="Entressafra 2026", key="rev_titulo")
        c1, c2, c3 = st.columns([1, 1, 0.9])
        with c1:
            dt_ini = st.date_input("Data início", value=None, key="rev_dt_ini")
        with c2:
            dt_fim = st.date_input("Data fim", value=None, key="rev_dt_fim")

        semanas_total = _calc_weeks(dt_ini, dt_fim)

        with c3:
            st.text_input(
                "Nº semanas",
                value=str(semanas_total),
                disabled=True,
                key="rev_total_weeks_display",
                help="Calculado automaticamente com base nas datas da revisão.",
            )

        if dt_ini and dt_fim and dt_fim < dt_ini:
            st.error("A data fim não pode ser menor que a data início.")
        elif dt_ini and dt_fim:
            k1, k2, k3 = st.columns(3)
            k1.metric("Período total", f"{(dt_fim - dt_ini).days + 1} dia(s)")
            k2.metric("Semanas geradas", semanas_total)
            k3.metric("Pronto para tendência/heatmap", "Sim")
            st.caption(
                "Ao sincronizar a matriz, as tarefas serão distribuídas por semana desta revisão. "
                "Isso alimenta automaticamente a tendência semanal e o heatmap do relatório executivo."
            )

            preview = _build_week_preview(dt_ini, dt_fim)
            if preview:
                with st.expander("Preview das semanas", expanded=True):
                    st.dataframe(preview, use_container_width=True, hide_index=True)

        submitted = st.button("Criar revisão", use_container_width=True, key="rev_submit", type="primary")

        if submitted:
            t = (titulo or "").strip()
            if not t:
                st.warning("Informe um título.")
                st.stop()
            if not dt_ini:
                st.warning("Informe a data de início.")
                st.stop()
            if not dt_fim:
                st.warning("Informe a data de fim.")
                st.stop()
            if dt_fim < dt_ini:
                st.warning("A data fim não pode ser menor que a data início.")
                st.stop()
            if semanas_total <= 0:
                st.warning("Não foi possível calcular as semanas da revisão.")
                st.stop()

            payload = {
                "tenant_id": tenant_id,
                "titulo": t,
                "status": "ativa",
                "data_inicio": str(dt_ini),
                "data_fim": str(dt_fim),
                "semanas_total": int(semanas_total),
            }

            try:
                svc = get_supabase_service()
                svc.table("revisoes").insert(payload).execute()
                st.toast("✓ Revisão criada", icon=":material/check_circle:")
                nav.rerun_keep_menu()
            except Exception as e:
                st.error(f"Erro ao criar revisão: {e}")

        st.divider()
        st.markdown("### Revisões existentes")
        revisoes = _fetch_revisoes(sb, tenant_id)

        if not revisoes:
            st.info("Nenhuma revisão criada.")
        else:
            for r in revisoes:
                with st.container(border=True):
                    rc1, rc2 = st.columns([0.7, 0.3])
                    with rc1:
                        st.markdown(f"**{r['titulo']}**")
                        st.caption(
                            f"Início: {r.get('data_inicio') or '—'} · Fim: {r.get('data_fim') or '—'} · Semanas: {r.get('semanas_total') or '—'}"
                        )
                        from src.ui.core.styles import status_badge
                        status_badge(r.get("status"))
                    with rc2:
                        ca, cb, cc = st.columns(3)
                        with ca:
                            if r.get("status") != "ativa":
                                if st.button("▶ Ativar", key=f"rev_active_{r['id']}", use_container_width=True, type="secondary"):
                                    try:
                                        sb.table("revisoes").update({"status": "fechada"}).eq("tenant_id", tenant_id).eq("status", "ativa").execute()
                                        sb.table("revisoes").update({"status": "ativa"}).eq("id", r["id"]).execute()
                                        st.toast("✓ Revisão ativada", icon=":material/check_circle:")
                                        nav.rerun_keep_menu()
                                    except Exception as e:
                                        st.error(f"Erro: {e}")
                        with cb:
                            if r.get("status") == "ativa":
                                if st.button("🔒 Fechar", key=f"rev_close_wiz_{r['id']}", use_container_width=True, type="primary"):
                                    st.session_state[f"wiz_fechar_{r['id']}"] = True
                        with cc:
                            if st.button("📦 Arquivar", key=f"rev_arch_{r['id']}", use_container_width=True):
                                try:
                                    sb.table("revisoes").update({"status": "arquivada"}).eq("id", r["id"]).execute()
                                    st.toast("✓ Arquivada", icon=":material/check_circle:")
                                    nav.rerun_keep_menu()
                                except Exception as e:
                                    st.error(f"Erro: {e}")

                    if st.session_state.get(f"wiz_fechar_{r['id']}"):
                        st.markdown("---")
                        st.markdown("#### 🔒 Checklist de fechamento")
                        st.caption("Verifique os itens abaixo antes de encerrar a revisão.")

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
                        concluidos = sum(1 for t in tarefas_rev if t.get("status") == "concluido")
                        travados = sum(1 for t in tarefas_rev if t.get("status") == "travado")
                        pendentes = sum(1 for t in tarefas_rev if t.get("status") in ("pendente", "em_andamento"))
                        pct_geral = round((concluidos / max(total_t, 1)) * 100)

                        wc1, wc2, wc3, wc4 = st.columns(4)
                        wc1.metric("Total tarefas", total_t)
                        wc2.metric("Concluídas", concluidos, delta=f"{pct_geral}%")
                        wc3.metric("Pendentes/And.", pendentes, delta_color="inverse" if pendentes else "off")
                        wc4.metric("Travadas", travados, delta_color="inverse" if travados else "off")

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
                                "Recomendamos resolver antes de fechar, mas você pode forçar o fechamento se necessário."
                            )

                        col_conf, _, col_cancel = st.columns([1, 1, 1])
                        with col_conf:
                            _lbl_conf = "✅ Confirmar fechamento" if not tem_bloqueio else "✅ Fechar mesmo assim"
                            if st.button(_lbl_conf, key=f"wiz_conf_{r['id']}", type="primary", use_container_width=True):
                                try:
                                    sb.table("revisoes").update({"status": "fechada"}).eq("id", r["id"]).execute()
                                    st.session_state.pop(f"wiz_fechar_{r['id']}", None)
                                    st.toast("🔒 Revisão encerrada.", icon=":material/lock:")
                                    nav.rerun_keep_menu()
                                except Exception as e:
                                    st.error(f"Erro ao fechar: {e}")
                        with col_cancel:
                            if st.button("✖ Cancelar", key=f"wiz_cancel_{r['id']}", use_container_width=True):
                                st.session_state.pop(f"wiz_fechar_{r['id']}", None)
                                st.rerun()

                    st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)

    with tab2:
        st.markdown("### Selecionar revisão")
        revisoes = _fetch_revisoes_min(sb, tenant_id)

        if not revisoes:
            st.info("Crie uma revisão primeiro.")
            return

        default_idx = 0
        for i, r in enumerate(revisoes):
            if r["status"] == "ativa":
                default_idx = i
                break

        rev_opt = [f"{r['titulo']} [{r['status']}]" for r in revisoes]
        sel = st.selectbox("Revisão", rev_opt, index=default_idx)
        revisao = revisoes[rev_opt.index(sel)]
        revisao_id = revisao["id"]

        st.divider()
        st.markdown("### Pré-checagens")
        grupos = _load_grupos(sb, tenant_id)
        if not grupos:
            st.warning("Você precisa criar grupos.")
            st.stop()

        eqs = _load_equipamentos(sb, tenant_id)
        total_eq = len(eqs)
        sem_grupo = [e for e in eqs if not e.get("grupo_id")]
        com_grupo = [e for e in eqs if e.get("grupo_id")]

        c1, c2, c3 = st.columns(3)
        c1.metric("Grupos ativos", len(grupos))
        c2.metric("Equipamentos ativos", total_eq)
        c3.metric("Sem grupo", len(sem_grupo))
        if sem_grupo:
            st.warning("Há equipamentos sem grupo. Eles serão ignorados na geração da matriz.")

        grupo_ids = [g["id"] for g in grupos]
        gs_map = _load_grupo_servicos(sb, tenant_id, grupo_ids)
        sem_template = [g for g in grupos if len(gs_map.get(g["id"], set())) == 0]
        if sem_template:
            st.warning(f"Grupos sem template: {', '.join(g['nome'] for g in sem_template[:8])}")

        st.divider()
        st.markdown("### Gerar / Sincronizar")
        st.caption(
            "A sincronização distribui tarefas por semana da revisão selecionada. Esses vínculos por semana são a base "
            "da tendência semanal e do heatmap no relatório executivo."
        )

        modo = st.radio(
            "Modo",
            [
                "Pré-visualizar impacto",
                "Criar faltantes (não altera existentes)",
                "Criar faltantes + marcar extras como Não se aplica",
            ],
            horizontal=False,
        )

        if st.button("Executar", type="primary", use_container_width=True):
            if not com_grupo:
                st.warning("Não há equipamentos com grupo para processar.")
                st.stop()

            eq_ids = [e["id"] for e in com_grupo]
            existing = _load_existing_tasks(sb, tenant_id, revisao_id, eq_ids)
            all_payload = []
            to_na = []
            prev_new = prev_existing = 0

            week_total = max(1, int(revisao.get("semanas_total") or 1))

            for e in com_grupo:
                gid = e["grupo_id"]
                servicos = gs_map.get(gid, set())
                if not servicos:
                    continue

                eq_existing = existing.get(e["id"], {})
                for idx, sid in enumerate(sorted(servicos), start=1):
                    if sid in eq_existing:
                        prev_existing += 1
                    else:
                        prev_new += 1
                        semana_ref = ((idx - 1) % week_total) + 1
                        all_payload.append(
                            {
                                "tenant_id": tenant_id,
                                "revisao_id": revisao_id,
                                "equipamento_id": e["id"],
                                "servico_id": sid,
                                "status": "pendente",
                                "semana": semana_ref,
                            }
                        )

                if modo == "Criar faltantes + marcar extras como Não se aplica":
                    extras = [row["id"] for sid, row in eq_existing.items() if sid not in servicos]
                    to_na.extend(extras)

            pc1, pc2, pc3 = st.columns(3)
            pc1.metric("Novas tarefas", prev_new)
            pc2.metric("Já existentes", prev_existing)
            pc3.metric("Extras → Não se aplica", len(to_na))

            if all_payload:
                weeks_dist = {}
                for item in all_payload:
                    s = int(item.get("semana") or 0)
                    weeks_dist[s] = weeks_dist.get(s, 0) + 1
                preview_rows = [{"Semana": f"Sem.{k}", "Tarefas previstas": v} for k, v in sorted(weeks_dist.items())]
                st.dataframe(preview_rows, hide_index=True, use_container_width=True)

            if modo == "Pré-visualizar impacto":
                st.info("Pré-visualização concluída. Nada foi alterado.")
            else:
                with st.spinner("Sincronizando matriz..."):
                    try:
                        if all_payload:
                            _insert_tasks(sb, all_payload)
                        if to_na:
                            _update_tasks_status(sb, to_na, status="nao_aplica")
                        time.sleep(0.2)
                        st.toast("✓ Matriz sincronizada.", icon=":material/check_circle:")
                        nav.rerun_keep_menu()
                    except Exception as e:
                        st.error(f"Erro ao sincronizar: {e}")
