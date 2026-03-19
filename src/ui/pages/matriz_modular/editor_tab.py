from __future__ import annotations

import streamlit as st

from src.ui.components.confirmations import confirmation_panel
from src.ui.components.forms import form_submit_button, validation_summary
from src.utils import nav
from src.utils.supabase_helpers import current_user_id
from src.ui.core.cache import bump_data_version
from src.ui.pages.matriz_runtime import risk_color as _risk_color


def render_editor_tab(
    *,
    sb,
    tenant_id,
    revisao_id,
    setor_to_services,
    eq_label_short,
    task_map,
    semana_sugerida,
):
    st.markdown("### ✏️ Edição rápida por célula")
    st.caption("Selecione frota, setor e serviço para atualizar etapas, status e observação.")

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

    # Item 9: mostrar toast de confirmação após rerun pós-save
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
                    bump_data_version()
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
                    bump_data_version()
                    try:
                        nav.rerun_keep_menu()
                    except Exception:
                        st.rerun()
                except Exception as e:
                    st.error(f"Erro: {e}")

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
        pass
