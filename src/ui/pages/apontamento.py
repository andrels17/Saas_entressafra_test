"""Apontamento — registro de status de tarefas por equipamento.

Melhorias Streamlit 1.42+:
  - st.data_editor com CheckboxColumn para etapas D/R/M (substitui formulários manuais)
  - @st.fragment para reruns parciais nos filtros (sem rerenderizar a página inteira)
  - st.status para feedback granular de carregamento
  - st.pills para filtro de setor sem rerun completo
  - st.metric para contadores de alterações
  - widget bind para sincronizar filtros com query params (URL compartilhável)
"""
from __future__ import annotations

import streamlit as st
import pandas as pd
from datetime import date

from src.utils.timezone import now_brt as _now_brt

from src.ui.components.filters import select_equipamento, select_grupo, select_revisao
from src.ui.components.feedback import notice_card
from src.ui.components.actions import download_action, primary_action_button
from src.ui.components.forms import validation_summary
from src.ui.core.styles import page_header as _ph
from src.ui.core.cache import bump_data_version
from src.ui.core.confirm_dialog import confirm_dialog
from src.ui.core.empty_state import empty_state
from src.ui.core.error_messages import show_supabase_error
from src.utils.ui_helpers import df_to_xlsx
from src.utils.supabase_helpers import sb_for_user, current_tenant_id, current_user_id
from src.db.supabase_client import get_supabase_anon
from src.utils.mobile import is_mobile
from src.utils.weeks import week_from_revisao, apontamento_datetime_iso, effective_week_for_apontamento


# ── Queries ─────────────────────────────────────────────────────────────


def _sb(token: str = ""):
    """Cliente Supabase para uso em funções cacheadas (sem acessar session_state)."""
    sb = get_supabase_anon()
    if token:
        sb.postgrest.auth(token)
    return sb


@st.cache_data(ttl=60, show_spinner=False)
def _load_revisoes(tenant_id: str, ver: str = "0", _token: str = "") -> list[dict]:
    return (
        _sb(_token).table("revisoes")
        .select("id,titulo,status,data_inicio,semanas_total")
        .eq("tenant_id", tenant_id)
        .order("created_at", desc=True)
        .execute()
        .data
    ) or []


@st.cache_data(ttl=60, show_spinner=False)
def _load_grupos(tenant_id: str, ver: str = "0", _token: str = "") -> list[dict]:
    return (
        _sb(_token).table("equip_grupos")
        .select("id,nome")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .order("nome")
        .execute()
        .data
    ) or []


@st.cache_data(ttl=30, show_spinner=False)
def _load_equipamentos(tenant_id: str, grupo_id: str, ver: str = "0", _token: str = "") -> list[dict]:
    return (
        _sb(_token).table("equipamentos")
        .select("id,frota,modelo,status")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .eq("grupo_id", grupo_id)
        .order("frota")
        .execute()
        .data
    ) or []


@st.cache_data(ttl=30, show_spinner=False)
def _load_tarefas(tenant_id: str, revisao_id: str, equipamento_id: str, ver: str = "0", _token: str = "") -> list[dict]:
    return (
        _sb(_token).table("tarefas_servico")
        .select("id,status,etapa_d,etapa_r,etapa_m,semana,observacao,servicos(id,nome,setor_id,setores(nome))")
        .eq("tenant_id", tenant_id)
        .eq("revisao_id", revisao_id)
        .eq("equipamento_id", equipamento_id)
        .execute()
        .data
    ) or []


# ── Helpers de UI ───────────────────────────────────────────────────────

def _build_editor_df(tarefas: list[dict], semana_default: int) -> pd.DataFrame:
    """Constrói o DataFrame para st.data_editor a partir das tarefas."""
    rows = []
    for t in tarefas:
        svc = t.get("servicos") or {}
        setor = (svc.get("setores") or {}).get("nome") or "Setor"
        rows.append({
            "_id": t["id"],
            "_status": t.get("status") or "pendente",
            "Setor": setor,
            "Serviço": svc.get("nome") or "—",
            "D": bool(t.get("etapa_d")),
            "R": bool(t.get("etapa_r")),
            "M": bool(t.get("etapa_m")),
            "Semana": int(t.get("semana") or semana_default),
            "Observação": t.get("observacao") or "",
        })
    return pd.DataFrame(rows)


def _df_to_changes(
        edited: pd.DataFrame,
        original: pd.DataFrame,
        user_id: str | None,
        *,
        data_inicio: date | None,
        semanas_total: int | None,
        data_apontamento: date | None = None) -> list[dict]:
    """Detecta linhas alteradas e monta payloads para upsert."""
    changes = []
    for idx in range(len(edited)):
        e = edited.iloc[idx]
        o = original.iloc[idx]
        # Normaliza observação: None e "" são equivalentes
        obs_e = (e["Observação"] or "").strip()
        obs_o = (o["Observação"] or "").strip()

        # Detecta qualquer mudança nas colunas editáveis
        changed = (
            bool(e["D"]) != bool(o["D"])
            or bool(e["R"]) != bool(o["R"])
            or bool(e["M"]) != bool(o["M"])
            or int(e["Semana"]) != int(o["Semana"])
            or obs_e != obs_o
        )
        if not changed:
            continue

        # Recalcula status a partir das etapas
        d, r, m = bool(e["D"]), bool(e["R"]), bool(e["M"])
        if d and r and m:
            status = "concluido"
        elif d or r or m:
            status = "em_andamento"
        else:
            # mantém status original se nenhuma etapa marcada
            status = o["_status"]

        effective_week = effective_week_for_apontamento(
            data_apontamento=data_apontamento,
            semana_operacional=int(e["Semana"]),
            data_inicio=data_inicio,
            semanas_total=semanas_total,
        )
        step_dt = apontamento_datetime_iso(
            data_apontamento=data_apontamento,
            semana_operacional=int(e["Semana"]),
            data_inicio=data_inicio,
            semanas_total=semanas_total,
        )

        changes.append({
            "id": e["_id"],
            "etapa_d": d,
            "etapa_r": r,
            "etapa_m": m,
            "dt_etapa_d": step_dt if d else None,
            "dt_etapa_r": step_dt if r else None,
            "dt_etapa_m": step_dt if m else None,
            "status": status,
            "semana": effective_week,
            "observacao": obs_e or None,
            "updated_by": user_id or None,
        })
    return changes


# ── Fragment: seletor de contexto (reroda só esta parte ao mudar) ───────

def _fragment_seletores(
        revisoes: list[dict]) -> tuple[dict | None, str | None, str | None]:
    """Seletor de revisão + grupo + equipamento em fragment isolado."""
    if not revisoes:
        empty_state(
            icon="◑", title="Nenhuma revisão criada",
            description="Crie uma revisão para começar a registrar tarefas.",
            action_label="Ir para Revisões", action_key="apt_goto_rev",
            nav_to="Admin - Revisões",
        )
        return None, None, None

    tenant_id = current_tenant_id()
    ver = str(st.session_state.get("data_version", "0"))

    # Revisão
    default_idx = next((i for i, r in enumerate(
        revisoes) if r["status"] == "ativa"), 0)
    revisao = select_revisao(
        revisoes,
        key="apt_revisao_sel",
        default_status="ativa",
        show_status_icon=True,
    )
    if not revisao:
        return None, None, None
    revisao_id = revisao["id"]

    data_inicio = None
    semanas_total = None
    try:
        if revisao.get("data_inicio"):
            data_inicio = date.fromisoformat(revisao["data_inicio"])
        semanas_total = int(revisao.get("semanas_total") or 0) or None
    except Exception:
        pass  # ignorado — operação opcional

    semana_default = week_from_revisao(
        _now_brt().date(), data_inicio, semanas_total)

    # Grupo
    grupos = _load_grupos(tenant_id, ver, st.session_state.get("sb_access_token", ""))
    if not grupos:
        empty_state(
            icon="⊕",
            title="Nenhum grupo cadastrado",
            description="Cadastre grupos de equipamentos para organizar as tarefas.",
            action_label="Ir para Grupos",
            action_key="apt_goto_grupos",
            nav_to="Admin - Grupos",
        )
        return None, None, None

    qp_grupo = st.query_params.get("grupo")
    default_grupo = qp_grupo if qp_grupo else None
    grupo_nome, grupo_id = select_grupo(
        grupos,
        key="apt_grupo_sel",
        default_id=default_grupo,
    )
    if not grupo_id:
        return None, None, None

    # Reseta equipamento quando o grupo muda
    prev_grupo = st.session_state.get("_apt_prev_grupo_id")
    grupo_mudou = prev_grupo and prev_grupo != grupo_id
    if grupo_mudou:
        # Remove o valor antigo E limpa o query param para evitar
        # que o selectbox herde o equipamento do grupo anterior
        st.session_state.pop("apt_eq_sel", None)
        try:
            del st.query_params["eq"]
        except Exception:
            pass
    st.session_state["_apt_prev_grupo_id"] = grupo_id
    st.query_params["grupo"] = grupo_id  # sincroniza URL

    # Equipamento
    equips = _load_equipamentos(tenant_id, grupo_id, ver, st.session_state.get("sb_access_token", ""))
    if not equips:
        st.info("Nenhum equipamento neste grupo.")
        return None, None, None

    # Só usa qp_eq se pertence ao grupo atual; se o grupo mudou ignora qp_eq
    qp_eq = None if grupo_mudou else st.query_params.get("eq")
    eq_ids_no_grupo = {e["id"] for e in equips if e.get("id")}
    default_eq = qp_eq if qp_eq in eq_ids_no_grupo else None
    eq_label, equipamento_id = select_equipamento(
        equips,
        key="apt_eq_sel",
        default_id=default_eq,
    )
    if not equipamento_id:
        return None, None, None
    st.query_params["eq"] = equipamento_id

    st.session_state["_apt_semana_default"] = int(semana_default)
    st.session_state["_apt_revisao_id"] = revisao_id
    st.session_state["_apt_revisao_titulo"] = revisao.get("titulo") or "Revisão"
    st.session_state["_apt_grupo_nome"] = grupo_nome or "Grupo"
    st.session_state["_apt_eq_label"] = eq_label or "Equipamento"
    st.session_state["_apt_equipamento_id"] = equipamento_id

    return revisao, revisao_id, equipamento_id


# ── Fragment: editor de tarefas ─────────────────────────────────────────


def _render_mobile_card(t: dict, sb, user_id: str, *, semana_default: int, data_inicio: date | None, semanas_total: int | None, data_apontamento: date | None = None) -> None:
    """Card de tarefa individual optimizado para toque em mobile."""
    svc = t.get("servicos") or {}
    setor = (svc.get("setores") or {}).get("nome") or "Setor"
    tid = t["id"]
    status = t.get("status") or "pendente"
    obs = t.get("observacao") or ""
    status_icon = {"concluido": "✅", "travado": "⛔", "em_andamento": "🔧", "pendente": "⏳"}.get(status, "⏳")

    with st.container(border=True):
        st.markdown(
            f"{status_icon} **{svc.get('nome') or '—'}**  \n"
            f'<span style="font-size:.8rem;color:#94A3B8">{setor}</span>',
            unsafe_allow_html=True,
        )
        if obs:
            st.caption(f"📝 {obs}")

        c1, c2, c3 = st.columns(3)
        new_d = c1.toggle("✂️ Desmontou", value=bool(t.get("etapa_d")), key=f"mob_d_{tid}")
        new_r = c2.toggle("🔍 Revisou",   value=bool(t.get("etapa_r")), key=f"mob_r_{tid}")
        new_m = c3.toggle("🔩 Montou",    value=bool(t.get("etapa_m")), key=f"mob_m_{tid}")

        if new_d != bool(t.get("etapa_d")) or new_r != bool(t.get("etapa_r")) or new_m != bool(t.get("etapa_m")):
            new_status = "concluido" if (new_d and new_r and new_m) else (
                "em_andamento" if (new_d or new_r or new_m) else status)
            effective_week = effective_week_for_apontamento(
                data_apontamento=data_apontamento,
                semana_operacional=int(t.get("semana") or semana_default or 1),
                data_inicio=data_inicio,
                semanas_total=semanas_total,
            )
            step_dt = apontamento_datetime_iso(
                data_apontamento=data_apontamento,
                semana_operacional=int(t.get("semana") or semana_default or 1),
                data_inicio=data_inicio,
                semanas_total=semanas_total,
            )
            try:
                sb.table("tarefas_servico").update({
                    "etapa_d": new_d, "etapa_r": new_r, "etapa_m": new_m,
                    "dt_etapa_d": step_dt if new_d else None,
                    "dt_etapa_r": step_dt if new_r else None,
                    "dt_etapa_m": step_dt if new_m else None,
                    "semana": effective_week,
                    "status": new_status, "updated_by": user_id or None,
                }).eq("id", tid).execute()
                bump_data_version()
                st.rerun()
            except Exception as e:
                st.error(f"Erro ao salvar: {e}")

        with st.expander("📝 Observação" + (f" — {obs[:30]}…" if len(obs) > 30 else (f" — {obs}" if obs else "")), expanded=False):
            new_obs = st.text_area("obs", value=obs, key=f"mob_obs_{tid}",
                                   label_visibility="collapsed", height=80)
            if st.button("Salvar", key=f"mob_obs_save_{tid}", use_container_width=True):
                norm = (new_obs or "").strip()
                if norm != obs.strip():
                    sb.table("tarefas_servico").update({
                        "observacao": norm or None, "updated_by": user_id or None
                    }).eq("id", tid).execute()
                    bump_data_version()
                    st.rerun()


@st.fragment
def _fragment_editor(
        tenant_id: str,
        revisao_id: str,
        equipamento_id: str) -> None:
    """Editor de tarefas — adaptativo desktop/mobile."""
    ver = str(st.session_state.get("data_version", "0"))
    mobile = is_mobile()

    with st.spinner("", show_time=False):
        tarefas = _load_tarefas(tenant_id, revisao_id, equipamento_id, ver, st.session_state.get("sb_access_token", ""))

    if not tarefas:
        notice_card(
            "Equipamento sem tarefas",
            "Nenhuma tarefa foi encontrada para o equipamento selecionado nesta revisão. Peça ao administrador para gerar ou sincronizar a matriz.",
            tone="warning",
        )
        return

    semana_default = st.session_state.get("_apt_semana_default", 1)
    user_id = current_user_id()

    # ── Filtros ───────────────────────────────────────────────────────────────
    # Chave única por contexto para evitar conflito de default entre mobile/desktop
    _pending_key = "apt_pending_mobile" if mobile else "apt_pending_desktop"
    _pending_default = True if mobile else False

    if mobile:
        show_pending = st.toggle("Somente pendentes/travados", value=_pending_default, key=_pending_key)
        semana_val = max(1, int(semana_default or 1))
    else:
        col_f1, col_f2 = st.columns([0.6, 0.4])
        with col_f1:
            show_pending = st.toggle("Somente pendentes/travados", value=_pending_default, key=_pending_key)
        with col_f2:
            semana_val = st.number_input(
                "Semana (sugestão)", min_value=1, value=max(1, int(semana_default or 1)),
                step=1, key="apt_semana_num")

    dtf1, dtf2 = st.columns([0.9, 1.1])
    with dtf1:
        usar_data_especifica = st.toggle(
            "Usar data específica",
            value=False,
            key="apt_use_specific_date",
            help="Quando ativado, a data escolhida será gravada no banco e a semana será recalculada a partir dela.",
        )
    with dtf2:
        data_apontamento = st.date_input(
            "🗓️ Data do apontamento",
            value=None,
            key="apt_specific_date",
            disabled=not usar_data_especifica,
            help="Se vazia, o sistema usa o primeiro dia da semana operacional selecionada.",
        ) if usar_data_especifica else None

    setores_disponiveis = sorted({
        (((t.get("servicos") or {}).get("setores") or {}).get("nome") or "Setor")
        for t in tarefas
    })
    setor_filtro = st.pills(
        "Setor", setores_disponiveis, selection_mode="multi",
        default=None, key="apt_setor_pills",
        label_visibility="collapsed" if len(setores_disponiveis) <= 1 else "visible",
    ) if len(setores_disponiveis) > 1 else None

    # ── Filtra ────────────────────────────────────────────────────────────────
    tarefas_filtradas = tarefas
    if show_pending:
        tarefas_filtradas = [t for t in tarefas_filtradas
                             if t.get("status") in ("pendente", "travado", "em_andamento")]
    if setor_filtro:
        tarefas_filtradas = [t for t in tarefas_filtradas
                             if (((t.get("servicos") or {}).get("setores") or {}).get("nome") or "Setor")
                             in setor_filtro]

    if not tarefas_filtradas:
        st.info("Nenhuma tarefa para os filtros selecionados.")
        return

    # ── Métricas ──────────────────────────────────────────────────────────────
    _total = len(tarefas_filtradas)
    _done  = sum(1 for t in tarefas_filtradas if t.get("status") == "concluido")
    _pend  = sum(1 for t in tarefas_filtradas if t.get("status") == "pendente")
    _trav  = sum(1 for t in tarefas_filtradas if t.get("status") == "travado")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total",       _total)
    m2.metric("Concluídos",  _done)
    m3.metric("Pendentes",   _pend)
    m4.metric("Travados",    _trav, delta_color="inverse" if _trav else "off")

    st.progress(
        _done / _total if _total else 0,
        text=f"Progresso: {_done}/{_total} ({100*_done//_total if _total else 0}%)",
    )

    # ── Mobile: cards de toque ────────────────────────────────────────────────
    if mobile:
        sb = sb_for_user()
        st.caption(f"{len(tarefas_filtradas)} tarefa(s) — toque nos toggles para registrar.")
        for t in tarefas_filtradas:
            _render_mobile_card(
                t,
                sb,
                user_id,
                semana_default=int(semana_val),
                data_inicio=data_inicio,
                semanas_total=semanas_total,
                data_apontamento=data_apontamento if usar_data_especifica else None,
            )
        return

    # ── Desktop: data_editor ──────────────────────────────────────────────────
    df_orig = _build_editor_df(tarefas_filtradas, int(semana_val))
    df_display = df_orig.drop(columns=["_id", "_status"])

    st.caption("Marque as etapas D (Desmontagem), R (Revisão), M (Montagem). "
               "O status é calculado automaticamente: D+R+M = Concluído.")

    edited = st.data_editor(
        df_display,
        column_config={
            "Setor":      st.column_config.TextColumn("Setor",     disabled=True),
            "Serviço":    st.column_config.TextColumn("Serviço",   disabled=True),
            "D":          st.column_config.CheckboxColumn("D",     help="Desmontagem concluída"),
            "R":          st.column_config.CheckboxColumn("R",     help="Revisão concluída"),
            "M":          st.column_config.CheckboxColumn("M",     help="Montagem concluída"),
            "Semana":     st.column_config.NumberColumn("Semana",  min_value=0, step=1),
            "Observação": st.column_config.TextColumn("Observação", max_chars=500),
        },
        hide_index=True,
        use_container_width=True,
        num_rows="fixed",
        key="apt_data_editor",
    )

    df_edited_full = df_orig.copy()
    df_edited_full[["Setor","Serviço","D","R","M","Semana","Observação"]] =         edited[["Setor","Serviço","D","R","M","Semana","Observação"]]

    changes = _df_to_changes(
        df_edited_full,
        df_orig,
        user_id,
        data_inicio=data_inicio,
        semanas_total=semanas_total,
        data_apontamento=data_apontamento if usar_data_especifica else None,
    )

    if not changes:
        st.info("Nenhuma alteração detectada.")
        return

    # Validação: travado exige observação
    invalidos = [c for c in changes if c["status"] ==
                 "travado" and not (c.get("observacao") or "").strip()]
    if invalidos:
        n = len(invalidos)
        validation_summary(
            [
                f"{'Um item foi marcado' if n == 1 else f'{n} itens foram marcados'} como Travado sem observação.",
                "Preencha o campo Observação antes de salvar.",
            ],
            title="Existem validações pendentes no apontamento",
        )
        st.stop()

    st.metric(
        "Alterações pendentes",
        len(changes),
        delta=f"{'item' if len(changes) == 1 else 'itens'} a salvar",
    )

    _exp_df = df_display.copy()
    _col_save, _col_xlsx = st.columns([0.75, 0.25])
    with _col_xlsx:
        try:
            download_action(
                "Exportar XLSX",
                data=df_to_xlsx(_exp_df, sheet_name="Apontamento"),
                file_name="apontamento.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="apt_xlsx_btn",
                help="Baixa a visão filtrada atual em Excel.",
            )
        except Exception:
            pass

    with _col_save:
        if primary_action_button("Salvar alterações", key="apt_save_btn",
                                  help="Aplica as alterações pendentes nas tarefas exibidas."):
            st.session_state["_apt_confirm_save"] = True

    n_changes = len(changes)
    confirmed = confirm_dialog(
        trigger_key="_apt_confirm_save",
        title="Salvar alterações?",
        body=f"Você está prestes a salvar **{n_changes} "
             f"{'alteração' if n_changes == 1 else 'alterações'}**. Confirma?",
        confirm_label="Salvar",
    )
    if confirmed:
        sb = sb_for_user()
        erros = 0
        with st.spinner("Salvando…", show_time=False):
            for ch in list(changes):
                tid = ch.pop("id")
                try:
                    sb.table("tarefas_servico").update(ch).eq("id", tid).execute()
                except Exception as e:
                    show_supabase_error(e, f"Tarefa {tid}")
                    erros += 1

        try:
            from src.utils.kpi_engine import invalidate_kpi_cache
            invalidate_kpi_cache()
        except Exception:
            pass
        bump_data_version()
        if erros == 0:
            st.toast(
                f"✅ {n_changes} {'alteração salva' if n_changes == 1 else 'alterações salvas'}.",
                icon=":material/check_circle:",
            )
        else:
            st.toast(f"⚠️ {erros} erro(s) ao salvar.", icon=":material/error:")
        st.rerun()

# ── Ponto de entrada público ────────────────────────────────────────────

_APT_RECENTES_KEY = "_apt_recentes"
_APT_RECENTES_MAX = 5


def _save_recente(revisao_id: str, revisao_titulo: str,
                  grupo_id: str, grupo_nome: str,
                  equipamento_id: str, eq_label: str) -> None:
    """Salva equipamento na lista de recentes da sessão."""
    recentes: list[dict] = list(st.session_state.get(_APT_RECENTES_KEY, []))
    entry = {
        "revisao_id": revisao_id, "revisao_titulo": revisao_titulo,
        "grupo_id": grupo_id, "grupo_nome": grupo_nome,
        "equipamento_id": equipamento_id, "eq_label": eq_label,
    }
    # Remove duplicata se já existe
    recentes = [r for r in recentes if r["equipamento_id"] != equipamento_id]
    recentes.insert(0, entry)
    st.session_state[_APT_RECENTES_KEY] = recentes[:_APT_RECENTES_MAX]


def _render_recentes() -> bool:
    """Exibe atalhos de equipamentos recentes. Retorna True se o usuário clicou num."""
    recentes: list[dict] = st.session_state.get(_APT_RECENTES_KEY, [])
    if not recentes:
        return False

    st.markdown(
        '<div style="font-size:0.68rem;font-weight:600;color:#8A9BAE;'
        'letter-spacing:0.08em;text-transform:uppercase;margin-bottom:6px">'
        '🕐 Acessados recentemente</div>',
        unsafe_allow_html=True,
    )
    cols = st.columns(min(len(recentes), _APT_RECENTES_MAX))
    for i, r in enumerate(recentes[:_APT_RECENTES_MAX]):
        with cols[i]:
            frota = r["eq_label"].split("—")[0].strip() if "—" in r["eq_label"] else r["eq_label"]
            if st.button(
                f"◉ {frota}\n{r['grupo_nome']}",
                key=f"apt_recente_{r['equipamento_id']}",
                use_container_width=True,
                type="tertiary",
                help=f"{r['eq_label']} · {r['grupo_nome']} · {r['revisao_titulo']}",
            ):
                st.query_params["grupo"] = r["grupo_id"]
                st.query_params["eq"] = r["equipamento_id"]
                st.session_state["_apt_revisao_id"] = r["revisao_id"]
                st.session_state["_apt_equipamento_id"] = r["equipamento_id"]
                st.session_state["_apt_grupo_nome"] = r["grupo_nome"]
                st.session_state["_apt_eq_label"] = r["eq_label"]
                st.session_state["_apt_revisao_titulo"] = r["revisao_titulo"]
                st.rerun()
    st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
    return False


def render_apontamento() -> None:
    _ph("◉", "Apontamento",
        "Registre o status de cada tarefa por equipamento, serviço e semana.")

    tenant_id = current_tenant_id()
    ver = str(st.session_state.get("data_version", "0"))
    revisoes = _load_revisoes(tenant_id, ver, st.session_state.get("sb_access_token", ""))

    # Atalhos de recentes (acima dos seletores)
    _render_recentes()

    # Fragment 1: seletores (reroda apenas ao mudar revisão/grupo/equipamento)
    _fragment_seletores(revisoes)

    revisao_id = st.session_state.get("_apt_revisao_id")
    equipamento_id = st.session_state.get("_apt_equipamento_id")

    if not revisao_id or not equipamento_id:
        return

    # Salva nos recentes sempre que há um equipamento ativo
    _save_recente(
        revisao_id=revisao_id,
        revisao_titulo=st.session_state.get("_apt_revisao_titulo") or "-",
        grupo_id=st.session_state.get("_apt_prev_grupo_id") or "",
        grupo_nome=st.session_state.get("_apt_grupo_nome") or "-",
        equipamento_id=equipamento_id,
        eq_label=st.session_state.get("_apt_eq_label") or "-",
    )

    st.divider()

    # Fragment 2: editor (reroda apenas ao editar tarefas)
    _fragment_editor(tenant_id, revisao_id, equipamento_id)
