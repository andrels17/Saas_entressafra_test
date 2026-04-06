"""Fragments de UI para notificações (cada seção da página como @st.fragment)."""
from __future__ import annotations
from html import escape as _h

import streamlit as st

from src.ui.components.actions import download_action
from src.ui.components.forms import (form_section, form_submit_button,
                                     validate_time_hhmm, validation_summary)
from src.ui.components.tables import titled_table
from src.ui.components.states import empty_message
from .data import resumo_por_grupo, load_manager_print_options, build_manager_print_zip


# ── Helper de download CSV ───────────────────────────────────────────────────

def _df_download(df, label: str, fname: str) -> None:
    if df.empty:
        return
    cols_pub = [c for c in df.columns if c != "dept_id"]
    st.download_button(
        f"⬇️ {label}",
        data=df[cols_pub].to_csv(index=False).encode("utf-8"),
        file_name=fname,
        mime="text/csv",
        use_container_width=True,
        key=f"dl_{fname}",
    )


# ── Resumo global ────────────────────────────────────────────────────────────

@st.fragment
def fragment_resumo(alertas: dict, revisao: dict) -> None:
    semana = alertas["semana_atual"]
    total_s = alertas["semanas_total"]
    n_trav = len(alertas["travados"])
    n_sem  = len(alertas["sem_inicio"])
    n_upd  = len(alertas["sem_update"])
    n_risc = len(alertas["risco_prazo"])

    st.markdown(
        f'<div style="font-size:.85rem;color:rgba(255,255,255,.55);margin-bottom:8px">'
        f'Semana <b style="color:#fff">{semana}</b> de <b style="color:#fff">{total_s}</b>'
        f' · Revisão: <b style="color:#FFD100">{_h(revisao.get("titulo", "—"))}</b></div>',
        unsafe_allow_html=True,
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Travados",   n_trav, delta="crítico" if n_trav else "ok",
              delta_color="inverse" if n_trav else "off")
    c2.metric("Sem início",  n_sem,  delta="atenção" if n_sem  else "ok",
              delta_color="inverse" if n_sem  else "off")
    c3.metric("Parados",     n_upd,  delta="atenção" if n_upd  else "ok",
              delta_color="inverse" if n_upd  else "off")
    c4.metric("Risco de prazo", n_risc, delta="atraso vs meta" if n_risc else "no prazo",
              delta_color="inverse" if n_risc else "off")

    if n_trav == 0 and n_sem == 0 and n_upd == 0 and n_risc == 0:
        empty_message("Nenhum alerta ativo com os thresholds configurados.", kind="success")


# ── Aba: travados ────────────────────────────────────────────────────────────

@st.fragment
def fragment_travados(df) -> None:
    cols_show = [c for c in ["Frota", "Modelo", "Grupo", "Setor", "Serviço",
                              "Dias travado", "Obs."] if c in df.columns]
    df_show = df[cols_show].sort_values("Dias travado", ascending=False) \
        if "Dias travado" in df.columns else df[cols_show]
    titled_table("Travados sem resolução", df_show,
                 caption=f"{len(df)} tarefa(s) travada(s) sem resolução." if not df.empty else None,
                 empty_message="Nenhum item travado no período configurado.",
                 column_config={
                     "Dias travado": st.column_config.NumberColumn(
                         "Dias travado", help="Dias desde última atualização"),
                 })
    _df_download(df, "Exportar CSV", "alertas_travados.csv")


# ── Aba: sem início ──────────────────────────────────────────────────────────

@st.fragment
def fragment_sem_inicio(df) -> None:
    cols_show = [c for c in ["Frota", "Modelo", "Grupo", "Setor", "Serviço",
                              "Dias sem update"] if c in df.columns]
    titled_table("Sem nenhum apontamento", df[cols_show],
                 caption=f"{len(df)} tarefa(s) sem nenhuma etapa marcada." if not df.empty else None,
                 empty_message="Todos os itens tiveram pelo menos um apontamento.")
    _df_download(df, "Exportar CSV", "alertas_sem_inicio.csv")


# ── Aba: parados ─────────────────────────────────────────────────────────────

@st.fragment
def fragment_parados(df) -> None:
    cols_show = [c for c in ["Frota", "Modelo", "Grupo", "Setor", "Serviço",
                              "Status", "Dias parado"] if c in df.columns]
    df_show = df[cols_show].sort_values("Dias parado", ascending=False) \
        if "Dias parado" in df.columns else df[cols_show]
    titled_table("⏸ Parados (sem atualização)", df_show,
                 caption=f"{len(df)} tarefa(s) sem atualização no período." if not df.empty else None,
                 empty_message="Nenhum item parado no período configurado.",
                 column_config={
                     "Dias parado": st.column_config.NumberColumn("Dias parado"),
                     "Status":      st.column_config.TextColumn("Status"),
                 })
    _df_download(df, "Exportar CSV", "alertas_parados.csv")


# ── Aba: risco de prazo ──────────────────────────────────────────────────────

@st.fragment
def fragment_risco_prazo(df) -> None:
    cols_show = [c for c in ["Frota", "Modelo", "Grupo", "% Atual", "% Esperado",
                              "Atraso (p.p.)", "Etapas feitas", "Etapas total"]
                 if c in df.columns]
    df_show = df[cols_show].sort_values("Atraso (p.p.)", ascending=False) \
        if "Atraso (p.p.)" in df.columns else df[cols_show]
    titled_table("Risco de não concluir no prazo", df_show,
                 caption=f"{len(df)} equipamento(s) com atraso acima de 15 p.p. em relação à meta."
                         if not df.empty else None,
                 empty_message="Todos os equipamentos estão dentro da meta linear.",
                 column_config={
                     "% Atual":    st.column_config.ProgressColumn("% Atual",    min_value=0, max_value=100),
                     "% Esperado": st.column_config.ProgressColumn("% Esperado", min_value=0, max_value=100),
                     "Atraso (p.p.)": st.column_config.NumberColumn(
                         "Atraso (p.p.)", help="Diferença entre esperado e atual"),
                 })
    _df_download(df, "Exportar CSV", "alertas_risco_prazo.csv")


# ── Aba: resumo por grupo ────────────────────────────────────────────────────

@st.fragment
def fragment_resumo_grupos(alertas: dict) -> None:
    st.markdown("### Resumo por grupo")
    df_res = resumo_por_grupo(alertas)
    if df_res.empty:
        st.info("Nenhum alerta encontrado para exibir por grupo.")
        return
    st.caption(f"{len(df_res)} grupo(s) com alertas ativos.")
    for _, row in df_res.iterrows():
        total = int(row.get("Total alertas", 0))
        trav  = int(row.get("Travados", 0))
        sem   = int(row.get("Sem início", 0))
        par   = int(row.get("Parados", 0))
        risc  = int(row.get("Risco prazo", 0))
        color = "#EF4444" if trav > 0 else ("#F59E0B" if (par + risc) > 0 else "#F59E0B")
        badges = []
        if trav: badges.append(
            f'<span style="background:rgba(239,68,68,.2);color:#EF4444;padding:2px 8px;border-radius:999px;font-size:.78rem">🚫 {trav} travado{"s" if trav > 1 else ""}</span>')
        if sem: badges.append(
            f'<span style="background:rgba(107,114,128,.2);color:#9CA3AF;padding:2px 8px;border-radius:999px;font-size:.78rem">⬜ {sem} sem início</span>')
        if par: badges.append(
            f'<span style="background:rgba(245,158,11,.2);color:#F59E0B;padding:2px 8px;border-radius:999px;font-size:.78rem">⏸ {par} parado{"s" if par > 1 else ""}</span>')
        if risc: badges.append(
            f'<span style="background:rgba(245,158,11,.2);color:#F59E0B;padding:2px 8px;border-radius:999px;font-size:.78rem">⚠️ {risc} risco prazo</span>')
        st.markdown(
            f'<div style="padding:10px 14px;border-radius:10px;border:1px solid rgba(255,255,255,.08);'
            f'background:rgba(255,255,255,.03);margin-bottom:8px">'
            f'<div style="font-size:.9rem;font-weight:700;margin-bottom:6px">'
            f'{_h(str(row["Grupo"]))} <span style="color:{color};font-size:.8rem">({total} alerta{"s" if total > 1 else ""})</span></div>'
            f'<div style="display:flex;flex-wrap:wrap;gap:6px">{"".join(badges)}</div>'
            f'</div>', unsafe_allow_html=True,
        )
    cols_pub = [c for c in df_res.columns if c != "dept_id"]
    st.download_button("⬇️ Exportar resumo por grupo (CSV)",
                       data=df_res[cols_pub].to_csv(index=False).encode("utf-8"),
                       file_name="alertas_resumo_grupos.csv", mime="text/csv",
                       use_container_width=True, key="dl_resumo_grupos")




# ── Aba: ZIP para impressão por gestor ──────────────────────────────────────


def _manager_ck_key(uid: str) -> str:
    return f"ntf_zip_mgr_{uid}"


def _group_ck_key(uid: str, gid: str) -> str:
    return f"ntf_zip_grp_{uid}_{gid}"


def _toggle_manager_groups(uid: str, group_ids: list[str]) -> None:
    checked = bool(st.session_state.get(_manager_ck_key(uid), False))
    for gid in group_ids:
        st.session_state[_group_ck_key(uid, gid)] = checked


def _sync_manager_from_groups(uid: str, group_ids: list[str]) -> None:
    selected = [bool(st.session_state.get(_group_ck_key(uid, gid), False)) for gid in group_ids]
    st.session_state[_manager_ck_key(uid)] = bool(selected) and all(selected)


@st.fragment
def fragment_zip_impressao_gestores(tenant_id: str, revisao_id: str, is_admin: bool) -> None:
    st.markdown('### 📦 ZIP para impressão por gestor')
    st.caption('Selecione os gestores e os grupos desejados para gerar um ZIP com 1 PDF da matriz por grupo.')

    if not is_admin:
        st.info('Apenas administradores podem gerar o ZIP em lote para impressão.')
        return

    managers = load_manager_print_options(tenant_id)
    if not managers:
        st.info('Nenhum gestor com grupos vinculados foi encontrado neste tenant.')
        return

    total_groups = 0
    selected_groups = 0
    selected_payload: list[dict] = []

    for manager in managers:
        uid = str(manager.get('user_id') or '')
        nome = manager.get('nome') or 'Gestor'
        email = manager.get('email') or ''
        groups = manager.get('grupos') or []
        group_ids = [str(g.get('grupo_id')) for g in groups if g.get('grupo_id')]
        total_groups += len(group_ids)

        mgr_key = _manager_ck_key(uid)
        if mgr_key not in st.session_state:
            st.session_state[mgr_key] = False
        for gid in group_ids:
            gkey = _group_ck_key(uid, gid)
            if gkey not in st.session_state:
                st.session_state[gkey] = False

        box_col, info_col = st.columns([0.08, 0.92])
        with box_col:
            st.checkbox(
                'Selecionar gestor',
                key=mgr_key,
                label_visibility='collapsed',
                on_change=_toggle_manager_groups,
                args=(uid, group_ids),
            )
        with info_col:
            with st.expander(f"**{nome}** — {len(group_ids)} grupo(s)", expanded=False):
                if email:
                    st.caption(email)
                dep_names = manager.get('departamento_nomes') or []
                if dep_names:
                    st.caption('Departamentos: ' + ', '.join(dep_names))

                selected_manager_groups = []
                for group in groups:
                    gid = str(group.get('grupo_id') or '')
                    if not gid:
                        continue
                    grp_key = _group_ck_key(uid, gid)
                    dep_nome = group.get('departamento_nome') or '—'
                    grp_nome = group.get('grupo_nome') or gid
                    st.checkbox(
                        f"{grp_nome} · {dep_nome}",
                        key=grp_key,
                        on_change=_sync_manager_from_groups,
                        args=(uid, group_ids),
                    )
                    if st.session_state.get(grp_key):
                        selected_manager_groups.append(group)

                if selected_manager_groups:
                    selected_groups += len(selected_manager_groups)
                    selected_payload.append({
                        'user_id': uid,
                        'nome': nome,
                        'email': email,
                        'grupos': selected_manager_groups,
                    })

    st.divider()
    col_info, col_action = st.columns([2, 1])
    with col_info:
        st.info(f'Selecionados: **{selected_groups} PDF(s)** de **{total_groups}** grupo(s) disponíveis.')
    with col_action:
        can_generate = selected_groups > 0
        if not can_generate:
            st.caption('Marque ao menos um grupo para habilitar o ZIP.')

    if st.button('Gerar ZIP para impressão', type='primary', use_container_width=True, disabled=not can_generate, key='ntf_zip_generate'):
        with st.spinner('Gerando PDFs e compactando ZIP...', show_time=True):
            zip_bytes, files_written, warnings = build_manager_print_zip(
                tenant_id=tenant_id,
                revisao_id=revisao_id,
                selected_managers=selected_payload,
                data_version=str(st.session_state.get('data_version', '0')),
                sb_access_token=str(st.session_state.get('sb_access_token', '')),
            )

        if files_written:
            st.success(f'ZIP pronto com {len(files_written)} PDF(s).')
            download_action(
                '⬇️ Baixar ZIP de impressão',
                data=zip_bytes,
                file_name=f'impressao_matriz_{revisao_id}.zip',
                mime='application/zip',
                key='ntf_zip_download',
                type='primary',
                use_container_width=True,
            )
        else:
            st.error('Nenhum PDF pôde ser gerado com a seleção atual.')

        if warnings:
            with st.expander('Ocorrências da geração', expanded=not files_written):
                for msg in warnings:
                    st.warning(msg)


# ── Aba: disparo manual de e-mail ────────────────────────────────────────────

@st.fragment
def fragment_disparo_manual(tenant_id: str, revisao_id: str, is_admin: bool,
                             dias_travado: int, dias_sem_update: int) -> None:
    if not is_admin:
        st.info("Apenas administradores podem disparar o envio de e-mails.")
        return

    smtp_ok, smtp_erro = True, ""
    try:
        from src.services.email.smtp_sender import _load_config_from_secrets
        _load_config_from_secrets()
    except Exception as e:
        smtp_ok, smtp_erro = False, str(e)

    col_smtp, col_dest = st.columns(2)
    with col_smtp:
        if smtp_ok:
            st.success("✅ SMTP configurado")
        else:
            st.error("❌ SMTP não configurado")
            with st.expander("Ver como configurar", expanded=False):
                st.markdown("**Microsoft (Outlook / Office 365):**")
                st.code('SMTP_HOST = "smtp.office365.com"\nSMTP_PORT = "587"\n'
                        'SMTP_USER = "seu@empresa.com.br"\nSMTP_PASSWORD = "sua_senha"\n'
                        'SMTP_FROM_NAME = "AgroSafra"', language="toml")
                st.markdown("**Gmail:**")
                st.code('SMTP_HOST = "smtp.gmail.com"\nSMTP_PORT = "587"\n'
                        'SMTP_USER = "seu@gmail.com"\nSMTP_PASSWORD = "sua_app_password"\n'
                        'SMTP_FROM_NAME = "AgroSafra"', language="toml")
                if smtp_erro:
                    st.caption(f"Erro atual: `{smtp_erro}`")

    with col_dest:
        try:
            from src.services.email.recipients import get_recipient_groups, get_executive_recipients
            groups_dest = get_recipient_groups(tenant_id)
            exec_recs = get_executive_recipients(tenant_id)
            total_gestor = sum(len(g.recipients) for g in groups_dest)
            total_exec = len(exec_recs)
            total_dest = total_gestor + total_exec
            if total_dest:
                st.success(f"✅ {total_gestor} gestor(es) · {total_exec} supervisor(es)/admin(s)")
            else:
                st.warning("⚠️ Nenhum destinatário encontrado")
        except Exception as e:
            st.warning(f"Não foi possível carregar destinatários: {e}")

    # Configuração de destinatários (expander separado)
    with st.expander("Configurar destinatários e tipo de relatório", expanded=False):
        _render_prefs_editor(tenant_id)

    st.divider()

    dry_run = st.toggle("Modo teste — gerar PDFs sem enviar e-mails", value=True,
                        key="ntf_email_dry",
                        help="Ative para validar a geração dos PDFs sem disparar nenhum e-mail.")
    if dry_run:
        st.info("PDFs serão gerados e validados, mas **nenhum e-mail será enviado**.")
    else:
        if not smtp_ok:
            st.error("Configure o SMTP antes de enviar e-mails reais.")
        else:
            st.warning("Modo real — os e-mails **serão enviados** aos responsáveis.")

    btn_label = "Testar geração de PDFs" if dry_run else "📧 Enviar relatórios agora"
    do_send = form_submit_button(btn_label, key="ntf_send_btn", use_container_width=False,
                                  help="Executa o envio imediato ou um dry-run.")

    if do_send:
        if not dry_run and not smtp_ok:
            st.error("Configure o SMTP no `secrets.toml` antes de enviar e-mails reais.")
            return
        from src.services.email.dispatcher import dispatch_relatorio_semanal
        log_lines: list[str] = []
        with st.spinner("Enviando relatórios…", show_time=True):
            try:
                result = dispatch_relatorio_semanal(
                    tenant_id=tenant_id, revisao_id=revisao_id,
                    dias_travado=dias_travado, dias_sem_update=dias_sem_update,
                    dry_run=dry_run, progress_callback=lambda msg: log_lines.append(msg),
                )
            except Exception as exc:
                st.error(f"Erro inesperado: {exc}")
                return

        if result.failed == 0 and result.sent > 0:
            st.success(f"✅ {'Teste concluído — ' if dry_run else ''}{result.sent} "
                       f"{'PDF(s) gerado(s) com sucesso. Nenhum e-mail enviado.' if dry_run else 'e-mail(s) enviado(s) com sucesso!'}")
        elif result.sent == 0 and result.skipped > 0:
            st.warning("Nenhum departamento com destinatário válido encontrado.")
        elif result.failed > 0:
            st.warning(f"Concluído com {result.failed} falha(s).")
        if result.errors:
            with st.expander("Erros", expanded=True):
                for err in result.errors:
                    st.error(err)
        if log_lines:
            with st.expander("Log completo", expanded=False):
                st.code("\n".join(log_lines))


def _render_prefs_editor(tenant_id: str) -> None:
    """Editor de preferências de e-mail por usuário."""
    st.caption(
        "**Tipo padrão por role:** gestor → relatório de departamento · "
        "supervisor/admin → relatório executivo consolidado.\n\n"
        "Altere individualmente abaixo para sobrescrever o padrão."
    )
    try:
        from src.services.email.recipients import get_all_users_with_prefs, save_email_pref
        users_prefs = get_all_users_with_prefs(tenant_id)
        if not users_prefs:
            st.info("Nenhum usuário encontrado para este tenant.")
            return

        role_icons = {"admin": "🔴", "supervisor": "🟣", "gestor": "🟠",
                      "executor": "🟡", "viewer": "⚪"}
        tipo_opts   = ["gestor", "executivo", "nenhum"]
        tipo_labels = {"gestor": "📋 Departamento", "executivo": "📊 Executivo",
                       "nenhum": "🚫 Não enviar"}
        changed = {}
        for u in users_prefs:
            icon = role_icons.get(u["role"], "⚪")
            label = f"{icon} **{u['nome']}** `{u['role']}` — {u['email']}"
            override_note = " _(override manual)_" if u["override"] else ""
            col_u, col_sel = st.columns([3, 2])
            with col_u:
                st.markdown(label + override_note)
            with col_sel:
                cur_tipo = u["tipo_relatorio"]
                novo = st.selectbox(
                    "Tipo", options=tipo_opts,
                    index=tipo_opts.index(cur_tipo) if cur_tipo in tipo_opts else 0,
                    format_func=lambda t: tipo_labels.get(t, t),
                    key=f"emailpref_{u['user_id']}", label_visibility="collapsed",
                )
                if novo != cur_tipo:
                    changed[u["user_id"]] = novo

        if changed:
            if form_submit_button("💾 Salvar preferências", key="save_email_prefs",
                                   help="Aplica o tipo de relatório definido para cada usuário."):
                ok = all(save_email_pref(tenant_id, uid, tipo, ativo=(tipo != "nenhum"))
                         for uid, tipo in changed.items())
                if ok:
                    st.success("Preferências salvas!")
                    st.rerun()
                else:
                    st.error("Erro ao salvar. Verifique se a tabela `tenant_email_prefs` existe.")
                    with st.expander("SQL para criar a tabela", expanded=True):
                        st.code("""
CREATE TABLE tenant_email_prefs (
  tenant_id       uuid NOT NULL,
  user_id         uuid NOT NULL,
  tipo_relatorio  text NOT NULL DEFAULT 'gestor',
  ativo           boolean NOT NULL DEFAULT true,
  updated_at      timestamptz DEFAULT now(),
  PRIMARY KEY (tenant_id, user_id)
);
ALTER TABLE tenant_email_prefs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all" ON tenant_email_prefs
  USING (true) WITH CHECK (true);
""", language="sql")
    except Exception as e_prefs:
        st.warning(f"Não foi possível carregar preferências: {e_prefs}")


# ── Aba: agendamento automático ──────────────────────────────────────────────

@st.fragment
def fragment_configurar_agendamento(tenant_id: str, is_admin: bool) -> None:
    from src.services.email.email_schedule import (
        DIAS_SEMANA_LABELS, PERIODICIDADE_LABELS, PERIODICIDADE_OPTS,
        ScheduleConfig, load_schedule_config, save_schedule_config,
    )
    form_section("Agendamento Automático",
                 "Configure o envio automático dos alertas operacionais e de prazo.")
    if not is_admin:
        st.info("Apenas administradores podem configurar o agendamento.")
        return

    with st.spinner("", show_time=False):
        cfg = load_schedule_config(tenant_id)

    col_status, col_prox = st.columns(2)
    with col_status:
        if cfg.ativo:
            st.success(f"Ativo — {cfg.descricao_humana()}")
        else:
            st.warning(f"Pausado — {cfg.descricao_humana()}")
    with col_prox:
        try:
            proximo = cfg.proximo_disparo_brt().strftime("%d/%m/%Y às %H:%M")
            st.info(f"Próximo disparo: **{proximo}** (Brasília)")
        except Exception:
            st.caption("Configure abaixo para ver o próximo disparo.")

    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        ativo = st.toggle("Agendamento ativo", value=cfg.ativo, key="sch_ativo")
        periodicidade_idx = PERIODICIDADE_OPTS.index(cfg.periodicidade) \
            if cfg.periodicidade in PERIODICIDADE_OPTS else 0
        periodicidade = st.selectbox("Periodicidade", options=PERIODICIDADE_OPTS,
                                     index=periodicidade_idx,
                                     format_func=lambda x: PERIODICIDADE_LABELS.get(x, x),
                                     key="sch_period")
        dias_travado = st.number_input("Alertar travado há (dias)", min_value=1,
                                       max_value=30, value=cfg.dias_travado, key="sch_dias_trav")
    with col2:
        hora_envio = st.text_input("Horário (HH:MM — Brasília)",
                                   value=cfg.hora_envio or "07:00", key="sch_hora")
        if periodicidade == "mensal":
            dia_mes = st.number_input("Dia do mês", min_value=1, max_value=28,
                                      value=cfg.dia_mes or 1, key="sch_dia_mes")
            dia_semana = cfg.dia_semana
        else:
            dia_semana = st.selectbox("Dia da semana", options=list(range(7)),
                                      index=cfg.dia_semana % 7,
                                      format_func=lambda i: DIAS_SEMANA_LABELS[i],
                                      key="sch_dia_sem")
            dia_mes = cfg.dia_mes
        dias_parado = st.number_input("Alertar parado há (dias)", min_value=1,
                                      max_value=30, value=cfg.dias_parado, key="sch_dias_par")

    schedule_errors = validate_time_hhmm(hora_envio, label="o horário de envio")
    col_save, col_preview = st.columns([1, 2])
    with col_save:
        if form_submit_button("💾 Salvar", key="sch_save",
                               help="Salva o agendamento automático."):
            if schedule_errors:
                validation_summary(schedule_errors, title="Corrija a configuração do agendamento")
            else:
                new_cfg = ScheduleConfig(
                    tenant_id=tenant_id, id=cfg.id, ativo=ativo,
                    periodicidade=periodicidade, dia_semana=int(dia_semana),
                    dia_mes=int(dia_mes), hora_envio=hora_envio.strip(),
                    dias_travado=int(dias_travado), dias_parado=int(dias_parado),
                    revisao_fixa=cfg.revisao_fixa,
                )
                if save_schedule_config(new_cfg):
                    st.success("Configuração salva!")
                    st.rerun()
                else:
                    st.error("Falha ao salvar. Verifique se a tabela `email_schedule_config` existe.")
    with col_preview:
        if not schedule_errors:
            try:
                preview_cfg = ScheduleConfig(
                    tenant_id=tenant_id, ativo=ativo, periodicidade=periodicidade,
                    dia_semana=int(dia_semana), dia_mes=int(dia_mes),
                    hora_envio=hora_envio.strip(), dias_travado=int(dias_travado),
                    dias_parado=int(dias_parado),
                )
                prox = preview_cfg.proximo_disparo_brt().strftime("%d/%m/%Y às %H:%M")
                st.caption(f"**Prévia:** {preview_cfg.descricao_humana()}")
                st.caption(f"Próximo disparo: {prox} (Brasília)")
            except Exception:
                pass

    with st.expander("Como configurar o scheduler", expanded=False):
        st.markdown(f"""
O `scheduler.py` e o GitHub Actions lêem esta configuração automaticamente do Supabase.

**Secrets necessários no repositório GitHub:**

| Secret | Valor |
|--------|-------|
| `SUPABASE_URL` | URL do Supabase |
| `SUPABASE_SERVICE_ROLE_KEY` | Chave service role |
| `SMTP_HOST` | ex: `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | e-mail remetente |
| `SMTP_PASSWORD` | senha ou App Password |
| `SCHEDULER_TENANT_ID` | `{tenant_id}` |
""")
