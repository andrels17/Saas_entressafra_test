from __future__ import annotations

import streamlit as st

from src.ui.components.confirmations import confirmation_panel
from src.ui.components.forms import form_submit_button, validation_summary
from src.utils import nav
from src.utils.supabase_helpers import current_user_id
from src.ui.core.cache_matrix import invalidate_matriz_cache
from src.ui.pages.matriz_runtime import risk_color as _risk_color


def _invalidate_after_editor_write() -> None:
    invalidate_matriz_cache()


def render_editor_tab(
    *,
    sb,
    tenant_id,
    revisao_id,
    grupo_id,
    setor_to_services,
    eq_label_short,
    task_map,
    semana_sugerida,
    eq_ocultos_set: set | None = None,
) -> None:
    st.markdown("### ✏️ Edição rápida por célula")
    st.caption("Selecione frota, setor e serviço para atualizar etapas, status e observação.")

    _ocultos = eq_ocultos_set or set()
    if _ocultos:
        with st.expander(f"👁 Equipamentos ocultos nesta revisão ({len(_ocultos)})", expanded=True):
            st.caption("Estes equipamentos não entram nos KPIs. Clique em Revelar quando pararem para manutenção.")
            try:
                from src.utils.eq_oculto import revelar_equipamento
                _ocultos_rows = (
                    sb.table("equipamentos")
                    .select("id,frota,modelo")
                    .eq("tenant_id", tenant_id)
                    .eq("grupo_id", grupo_id)
                    .in_("id", list(_ocultos))
                    .order("frota")
                    .execute()
                    .data
                ) or []
                for _eq in _ocultos_rows:
                    _eid = _eq.get("id")
                    _label = f"{_eq.get('frota') or '—'} — {_eq.get('modelo') or ''}".strip(" —")
                    _c1, _c2 = st.columns([3, 1])
                    _c1.markdown(f"**{_label}**")
                    with _c2:
                        if st.button("👁 Revelar", key=f"rev_oculto_{_eid}",
                                     use_container_width=True, type="primary"):
                            revelar_equipamento(sb, tenant_id, revisao_id, _eid)
                            _invalidate_after_editor_write()
                            st.toast(f"{_label} revelado ✅")
                            st.rerun()
            except Exception as _e:
                st.warning(f"Erro ao carregar ocultos: {_e}")

    ed_c1, ed_c2, ed_c3 = st.columns([1, 1, 1])
    with ed_c1:
        equip_choices_short = {eq_label_short[eid]: eid for eid in eq_label_short}
        esl = st.selectbox("🚜 Frota", list(equip_choices_short.keys()), key="mat_eq_sel")
        equip_sel = equip_choices_short[esl]
    with ed_c2:
        setores_ed = sorted(setor_to_services.keys(), key=lambda x: x.lower())
        if setores_ed:
            setor_ed = st.selectbox("📂 Setor", setores_ed, key="mat_setor_sel")
        else:
            st.info("Sem setores disponíveis neste grupo.")
            setor_ed = None
    with ed_c3:
        if setor_ed:
            svs_ed = sorted(setor_to_services[setor_ed], key=lambda x: (x.get("nome") or "").lower())
            svc_choices = {s.get("nome") or str(s.get("id")): s["id"] for s in svs_ed if s.get("id")}
            if svc_choices:
                svc_name = st.selectbox("🔧 Serviço", list(svc_choices.keys()), key="mat_srv_sel")
                svc_sel = svc_choices[svc_name]
            else:
                st.info("Sem serviços neste setor.")
                svc_sel = None
                svc_name = ""
        else:
            svc_sel = None
            svc_name = ""

    task_ed = None
    if not setor_ed or not svc_sel:
        st.info("Selecione um setor e serviço válidos para continuar.")
        return

    try:
        from src.utils.eq_oculto import ocultar_equipamento
        _already_oculto = equip_sel in (_ocultos or set())
        _any_done_rows = (
            sb.table("tarefas_servico")
            .select("id").eq("tenant_id", tenant_id)
            .eq("revisao_id", revisao_id).eq("equipamento_id", equip_sel)
            .eq("etapa_d", True).limit(1).execute().data
        ) or []
        if not _already_oculto and not _any_done_rows:
            if st.button("⊘ Ocultar desta revisão", key=f"ocultar_{equip_sel}",
                         help="Oculta o equipamento dos KPIs enquanto não parar para manutenção."):
                try:
                    ocultar_equipamento(sb, tenant_id, revisao_id, equip_sel, current_user_id())
                    _invalidate_after_editor_write()
                    st.toast("Equipamento ocultado ✅ — será revelado automaticamente quando uma etapa for marcada.")
                    st.rerun()
                except Exception as _write_exc:
                    from src.utils.observability import log_error
                    log_error(
                        _write_exc,
                        context="editor_tab.ocultar_equipamento",
                        table="eq_ocultos",
                        extra={"equipamento_id": equip_sel, "revisao_id": revisao_id},
                    )
                    st.error("Não foi possível ocultar o equipamento. Tente novamente.")
    except Exception as _setup_exc:
        from src.utils.observability import log_error
        log_error(
            _setup_exc,
            context="editor_tab.ocultar_setup",
            extra={"equipamento_id": equip_sel},
        )

    _saved_key = f"mat_just_saved_{equip_sel}_{svc_sel}"
    if st.session_state.pop(_saved_key, False):
        st.toast("✅ Alterações salvas com sucesso!", icon="✅")

    task_rows_ed = (
        sb.table("tarefas_servico")
        .select("id,status,semana,observacao,etapa_d,etapa_r,etapa_m")
        .eq("tenant_id", tenant_id)
        .eq("revisao_id", revisao_id)
        .eq("equipamento_id", equip_sel)
        .eq("servico_id", svc_sel)
        .limit(1)
        .execute()
        .data
    ) or []
    task_ed = task_rows_ed[0] if task_rows_ed else None

    if not task_ed:
        st.warning("⚠️ Tarefa não encontrada para esta combinação.")
        return

    st.divider()
    cur_d = bool(task_ed.get("etapa_d"))
    cur_r = bool(task_ed.get("etapa_r"))
    cur_m = bool(task_ed.get("etapa_m"))
    cur_pct = round(((int(cur_d) + int(cur_r) + int(cur_m)) / 3) * 100)
    _risk_color(cur_pct)

    def _badge(label, done):
        if done:
            return (
                f'<span style="padding:3px 10px;border-radius:999px;'
                f'background:rgba(18,183,106,.2);color:#12B76A;font-size:.8rem">✓ {label}</span>'
            )
        return (
            f'<span style="padding:3px 10px;border-radius:999px;'
            f'background:rgba(255,255,255,.06);color:rgba(255,255,255,.4);font-size:.8rem">✗ {label}</span>'
        )

    badge_d = _badge("D", cur_d)
    badge_r = _badge("R", cur_r)
    badge_m = _badge("M", cur_m)
    status_label = "Concluído" if cur_pct == 100 else ("Pendente" if cur_pct == 0 else "Em andamento")

    info_col1, info_col2 = st.columns([2, 1])
    with info_col1:
        st.markdown(
            f'<div style="padding:10px 14px;border-radius:10px;border:1px solid rgba(255,255,255,.1);'
            f'background:rgba(255,255,255,.04);margin-bottom:8px">'
            f'<div style="font-size:.8rem;color:rgba(255,255,255,.5);margin-bottom:4px">Estado atual</div>'
            f'<div style="display:flex;gap:12px;align-items:center">'
            f'<span style="font-size:.9rem">Frota <b>{esl}</b></span>'
            f'<span style="color:rgba(255,255,255,.4)">·</span>'
            f'<span style="font-size:.9rem">{setor_ed}</span>'
            f'<span style="color:rgba(255,255,255,.4)">·</span>'
            f'<span style="font-size:.9rem">{svc_name}</span>'
            f'</div>'
            f'<div style="margin-top:6px;display:flex;gap:6px">{badge_d}{badge_r}{badge_m}</div></div>',
            unsafe_allow_html=True,
        )
    with info_col2:
        st.metric("Progresso atual", f"{cur_pct}%", delta=status_label)

    st.markdown("#### Atualizar etapas")
    cD, cR, cM, cSem = st.columns([1, 1, 1, 1])
    with cD:
        etapa_d = st.checkbox("✅ Desmontou (D)", value=cur_d, key="mat_ed_d")
    with cR:
        etapa_r = st.checkbox("✅ Revisou (R)", value=cur_r, key="mat_ed_r")
    with cM:
        etapa_m = st.checkbox("✅ Montou (M)", value=cur_m, key="mat_ed_m")
    with cSem:
        semana_default = int(task_ed.get("semana") or semana_sugerida)
        nsem = st.number_input(
            "📅 Semana",
            min_value=0,
            value=semana_default,
            step=1,
            key="mat_sem",
            help=(
                f"Semana sugerida automaticamente: {semana_sugerida}. "
                "Altere se precisar registrar em outra semana."
            ),
        )

    st.caption("Marcar D+R+M atualiza o status para Concluído automaticamente.")

    status_options = [
        ("pendente", "⏳ Pendente"),
        ("em_andamento", "🔄 Em andamento"),
        ("concluido", "✅ Concluído"),
        ("travado", "🚫 Travado"),
        ("nao_aplica", "➖ Não aplica"),
    ]
    status_keys = [k for k, _ in status_options]
    status_labels = [v for _, v in status_options]
    status_index = status_keys.index(task_ed["status"]) if task_ed.get("status") in status_keys else 0

    st_col1, st_col2 = st.columns([1, 2])
    with st_col1:
        new_label = st.selectbox("📌 Status", status_labels, index=status_index, key="mat_st_sel")
        new_status = status_keys[status_labels.index(new_label)]
    with st_col2:
        nobs = st.text_area(
            "💬 Observação",
            value=task_ed.get("observacao") or "",
            key="mat_obs_ed",
            height=80,
            placeholder="Descreva impedimentos, peças aguardadas, ocorrências...",
        )

    sv_a, sv_b, _ = st.columns([1, 1, 2])
    with sv_a:
        save_quick = form_submit_button(
            "💾 Salvar",
            key="mat_save_ed",
            help="Salva as etapas, semana, status e observação da tarefa selecionada.",
        )
        if save_quick:
            effective_status = "concluido" if etapa_d and etapa_r and etapa_m else new_status
            quick_errors = []
            if effective_status == "travado" and not (nobs or "").strip():
                quick_errors.append("Preencha a observação antes de salvar uma tarefa como Travado.")

            if quick_errors:
                validation_summary(quick_errors, title="Corrija o formulário da tarefa")
            else:
                try:
                    (
                        sb.table("tarefas_servico")
                        .update(
                            {
                                "etapa_d": bool(etapa_d),
                                "etapa_r": bool(etapa_r),
                                "etapa_m": bool(etapa_m),
                                "status": effective_status,
                                "semana": int(nsem) if int(nsem) > 0 else None,
                                "observacao": nobs.strip() or None,
                                "updated_by": current_user_id() or None,
                            }
                        )
                        .eq("id", task_ed["id"])
                        .execute()
                    )
                    _invalidate_after_editor_write()
                    st.session_state[f"mat_just_saved_{equip_sel}_{svc_sel}"] = True
                    try:
                        nav.rerun_keep_menu()
                    except Exception:
                        st.rerun()
                except Exception as e:
                    st.error(f"Erro: {e}")
    with sv_b:
        if (task_ed.get("observacao") or "").strip():
            if st.button("🗑️ Limpar obs.", use_container_width=True, key="mat_clear_obs"):
                st.session_state["confirm_clear_obs_matriz"] = True
                st.rerun()

            if confirmation_panel(
                state_key="confirm_clear_obs_matriz",
                title="Confirma limpar a observação desta tarefa?",
                body="A observação atual será removida imediatamente da tarefa selecionada.",
                confirm_label="Limpar observação",
            ):
                try:
                    sb.table("tarefas_servico").update({"observacao": None}).eq("id", task_ed["id"]).execute()
                    st.toast("Observação removida.")
                    _invalidate_after_editor_write()
                    try:
                        nav.rerun_keep_menu()
                    except Exception:
                        st.rerun()
                except Exception as e:
                    st.error(f"Erro: {e}")


    # ── J: Histórico de alterações ──────────────────────────────────────────
    st.markdown("---")
    with st.expander("🕐 Histórico de alterações desta tarefa", expanded=False):
        try:
            hist_rows = (
                sb.table("audit_logs")
                .select("event,actor_email,created_at,metadata")
                .eq("tenant_id", tenant_id)
                .eq("resource_id", task_ed["id"])
                .order("created_at", desc=True)
                .limit(20)
                .execute()
                .data
            ) or []
        except Exception:
            hist_rows = []

        if not hist_rows:
            # Fallback: mostrar updated_by e updated_at da própria tarefa
            try:
                task_detail = (
                    sb.table("tarefas_servico")
                    .select("updated_by,updated_at,etapa_d,etapa_r,etapa_m,status")
                    .eq("id", task_ed["id"])
                    .limit(1)
                    .execute()
                    .data
                ) or []
                if task_detail:
                    t = task_detail[0]
                    updated_at = t.get("updated_at") or ""
                    updated_by = t.get("updated_by") or "—"
                    if updated_at:
                        try:
                            import pandas as pd
                            dt = pd.to_datetime(updated_at, utc=True).tz_convert("America/Sao_Paulo")
                            updated_at = dt.strftime("%d/%m/%Y %H:%M")
                        except Exception:
                            pass
                    etapas = " · ".join(
                        f for f, v in [("D", t.get("etapa_d")), ("R", t.get("etapa_r")), ("M", t.get("etapa_m"))] if v
                    ) or "nenhuma"
                    st.markdown(
                        f"**Última alteração:** {updated_at}  ·  "
                        f"**Por:** `{updated_by}`  ·  "
                        f"**Etapas:** {etapas}  ·  "
                        f"**Status:** {t.get('status', '—')}"
                    )
                else:
                    st.caption("Sem histórico registrado para esta tarefa.")
            except Exception:
                st.caption("Histórico não disponível.")
        else:
            for h in hist_rows:
                import pandas as pd
                try:
                    dt = pd.to_datetime(h.get("created_at", ""), utc=True).tz_convert("America/Sao_Paulo")
                    when = dt.strftime("%d/%m/%Y %H:%M")
                except Exception:
                    when = h.get("created_at", "")[:16]
                actor = h.get("actor_email") or h.get("actor_id") or "—"
                event = h.get("event") or "alteração"
                meta = h.get("metadata") or {}
                detail = ", ".join(f"{k}: {v}" for k, v in meta.items() if k not in ("tenant_id",)) if meta else ""
                st.markdown(f"- **{when}** · `{actor}` · _{event}_{f' · {detail}' if detail else ''}")

    st.markdown("---")
    try:
        from src.ui.components.comentarios import render_comentarios

        user_name = st.session_state.get("sb_user_nome") or "Usuário"
        render_comentarios(
            tenant_id,
            task_ed["id"],
            user_nome=user_name,
            key_prefix=f"mtz_{equip_sel}_{svc_sel}_",
        )
    except Exception:
        pass  # comentários são opcionais — tabela pode não existir


def render_bulk_editor(
    *,
    sb,
    tenant_id,
    revisao_id,
    setor_to_services,
    task_map,
    eqs,
    eq_label_short,
    semana_sugerida,
) -> None:
    """Aba de edição em lote — marca D/R/M para todos os equipamentos de um serviço."""
    st.markdown("### ⚡ Edição em lote por serviço")
    st.caption("Selecione um serviço e marque etapas para múltiplos equipamentos de uma vez.")

    # Setor e serviço
    bl_c1, bl_c2 = st.columns([1, 1])
    with bl_c1:
        setores = sorted(setor_to_services.keys(), key=lambda x: x.lower())
        if not setores:
            st.info("Sem setores disponíveis.")
            return
        setor_bulk = st.selectbox("📂 Setor", setores, key="bulk_setor_sel")
    with bl_c2:
        svs_bulk = sorted(setor_to_services[setor_bulk], key=lambda x: (x.get("nome") or "").lower())
        svc_choices = {s.get("nome") or str(s.get("id")): s["id"] for s in svs_bulk if s.get("id")}
        if not svc_choices:
            st.info("Sem serviços neste setor.")
            return
        svc_bulk_name = st.selectbox("🔧 Serviço", list(svc_choices.keys()), key="bulk_svc_sel")
        svc_bulk_id = svc_choices[svc_bulk_name]

    st.divider()
    # Etapas a aplicar
    bc1, bc2, bc3, bc4 = st.columns([1, 1, 1, 1])
    with bc1: bulk_d = st.checkbox("✅ Desmontou (D)", key="bulk_ed_d")
    with bc2: bulk_r = st.checkbox("✅ Revisou (R)", key="bulk_ed_r")
    with bc3: bulk_m = st.checkbox("✅ Montou (M)", key="bulk_ed_m")
    with bc4:
        bulk_sem = st.number_input(
            "📅 Semana", min_value=0, value=int(semana_sugerida), step=1, key="bulk_sem",
        )

    if not any([bulk_d, bulk_r, bulk_m]):
        st.info("Selecione pelo menos uma etapa para aplicar em lote.")
        return

    # Mostrar preview de quais equipamentos serão afetados
    affected = []
    for eq in eqs:
        eid = str(eq["id"])
        t = task_map.get((eid, str(svc_bulk_id))) or {}
        needs_update = (
            (bulk_d and not t.get("etapa_d")) or
            (bulk_r and not t.get("etapa_r")) or
            (bulk_m and not t.get("etapa_m"))
        )
        if needs_update:
            affected.append((eid, t.get("id"), eq_label_short.get(eid, eid)))

    if not affected:
        st.success("✅ Todos os equipamentos já têm as etapas selecionadas marcadas.")
        return

    st.info(f"**{len(affected)}** equipamento(s) serão atualizados para {svc_bulk_name}.")
    with st.expander("Ver equipamentos afetados", expanded=False):
        for _, _, lbl in affected:
            st.markdown(f"- Frota **{lbl}**")

    if st.button(
        f"⚡ Aplicar em lote ({len(affected)} equipamentos)",
        key="bulk_apply_btn",
        type="primary",
        use_container_width=True,
    ):
        from datetime import datetime, timezone
        from src.ui.pages.matriz_runtime import bulk_update_tasks as _bulk_update_tasks

        now_iso = datetime.now(timezone.utc).isoformat()
        updates = []
        inserts = []

        for eid, tid, lbl in affected:
            if tid:
                upd = {"id": tid, "updated_by": current_user_id() or None}
                if bulk_d: upd["etapa_d"] = True; upd["dt_etapa_d"] = now_iso
                if bulk_r: upd["etapa_r"] = True; upd["dt_etapa_r"] = now_iso
                if bulk_m: upd["etapa_m"] = True; upd["dt_etapa_m"] = now_iso
                if bulk_sem > 0: upd.setdefault("semana", int(bulk_sem))
                updates.append(upd)
            else:
                row = {
                    "tenant_id": tenant_id,
                    "revisao_id": revisao_id,
                    "equipamento_id": eid,
                    "servico_id": svc_bulk_id,
                    "etapa_d": bool(bulk_d),
                    "etapa_r": bool(bulk_r),
                    "etapa_m": bool(bulk_m),
                    "status": "concluido" if (bulk_d and bulk_r and bulk_m) else "em_andamento",
                    "updated_by": current_user_id() or None,
                }
                if bulk_sem > 0: row["semana"] = int(bulk_sem)
                inserts.append(row)

        ok = 0
        failed = 0
        if inserts:
            try:
                sb.table("tarefas_servico").insert(inserts).execute()
                ok += len(inserts)
            except Exception as _e:
                st.error(f"Erro ao criar tarefas: {_e}")
                failed += len(inserts)

        if updates:
            _ok, _fail = _bulk_update_tasks(sb, updates)
            ok += _ok
            failed += _fail

        if ok > 0:
            st.success(f"✅ {ok} equipamento(s) atualizados com sucesso!")
            _invalidate_after_editor_write()
            try:
                nav.rerun_keep_menu()
            except Exception:
                st.rerun()
        if failed:
            st.warning(f"⚠️ {failed} atualização(ões) falharam.")
