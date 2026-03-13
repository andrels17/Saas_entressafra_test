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
from collections import defaultdict
from datetime import date

from src.utils.timezone import now_brt as _now_brt

from src.ui.core.styles import page_header as _ph
from src.ui.core.confirm_dialog import confirm_dialog
from src.ui.core.empty_state import empty_state
from src.ui.core.error_messages import show_supabase_error
from src.utils.ui_helpers import df_to_xlsx, status_badge
from src.utils.supabase_helpers import sb_for_user, current_tenant_id, current_role, current_user_id
from src.utils.weeks import week_from_revisao
from src.utils import nav
from src.utils.mobile import is_mobile


# ── Queries ───────────────────────────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def _load_revisoes(_tenant_id: str, _ver: str = "0") -> list[dict]:
    sb = sb_for_user()
    return (
        sb.table("revisoes")
        .select("id,titulo,status,data_inicio,semanas_total")
        .eq("tenant_id", _tenant_id)
        .order("created_at", desc=True)
        .execute()
        .data
    ) or []


@st.cache_data(ttl=60, show_spinner=False)
def _load_grupos(_tenant_id: str, _ver: str = "0") -> list[dict]:
    sb = sb_for_user()
    return (
        sb.table("equip_grupos")
        .select("id,nome")
        .eq("tenant_id", _tenant_id)
        .eq("ativo", True)
        .order("nome")
        .execute()
        .data
    ) or []


@st.cache_data(ttl=30, show_spinner=False)
def _load_equipamentos(_tenant_id: str, _grupo_id: str, _ver: str = "0") -> list[dict]:
    sb = sb_for_user()
    return (
        sb.table("equipamentos")
        .select("id,frota,modelo,status")
        .eq("tenant_id", _tenant_id)
        .eq("ativo", True)
        .eq("grupo_id", _grupo_id)
        .order("frota")
        .execute()
        .data
    ) or []


@st.cache_data(ttl=30, show_spinner=False)
def _load_tarefas(_tenant_id: str, _revisao_id: str, _equipamento_id: str, _ver: str = "0") -> list[dict]:
    sb = sb_for_user()
    return (
        sb.table("tarefas_servico")
        .select("id,status,etapa_d,etapa_r,etapa_m,semana,observacao,servicos(id,nome,setor_id,setores(nome))")
        .eq("tenant_id", _tenant_id)
        .eq("revisao_id", _revisao_id)
        .eq("equipamento_id", _equipamento_id)
        .execute()
        .data
    ) or []


# ── Helpers de UI ─────────────────────────────────────────────────────────────

def _build_editor_df(tarefas: list[dict], semana_default: int) -> pd.DataFrame:
    """Constrói o DataFrame para st.data_editor a partir das tarefas."""
    rows = []
    for t in tarefas:
        svc = t.get("servicos") or {}
        setor = (svc.get("setores") or {}).get("nome") or "Setor"
        rows.append({
            "_id":       t["id"],
            "_status":   t.get("status") or "pendente",
            "Setor":     setor,
            "Serviço":   svc.get("nome") or "—",
            "D":         bool(t.get("etapa_d")),
            "R":         bool(t.get("etapa_r")),
            "M":         bool(t.get("etapa_m")),
            "Semana":    int(t.get("semana") or semana_default),
            "Observação": t.get("observacao") or "",
        })
    return pd.DataFrame(rows)


def _df_to_changes(edited: pd.DataFrame, original: pd.DataFrame, user_id: str | None) -> list[dict]:
    """Detecta linhas alteradas e monta payloads para upsert."""
    changes = []
    for idx in range(len(edited)):
        e = edited.iloc[idx]
        o = original.iloc[idx]
        # Detecta qualquer mudança nas colunas editáveis
        changed = (
            bool(e["D"]) != bool(o["D"])
            or bool(e["R"]) != bool(o["R"])
            or bool(e["M"]) != bool(o["M"])
            or int(e["Semana"]) != int(o["Semana"])
            or str(e["Observação"]) != str(o["Observação"])
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

        changes.append({
            "id":          e["_id"],
            "etapa_d":     d,
            "etapa_r":     r,
            "etapa_m":     m,
            "status":      status,
            "semana":      int(e["Semana"]) or None,
            "observacao":  str(e["Observação"]) or None,
            "updated_by":  user_id or None,
        })
    return changes


# ── Fragment: seletor de contexto (reroda só esta parte ao mudar) ─────────────

@st.fragment
def _fragment_seletores(revisoes: list[dict]) -> tuple[dict | None, str | None, str | None]:
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
    default_idx = next((i for i, r in enumerate(revisoes) if r["status"] == "ativa"), 0)
    _STATUS_ICON = {"ativa": "🟢", "fechada": "⚪", "arquivada": "🗄️"}
    rev_labels  = [
        f"{_STATUS_ICON.get(r.get('status', ''), '○')} {r['titulo']} [{r['status']}]"
        for r in revisoes
    ]
    rev_sel     = st.selectbox("Revisão", rev_labels, index=default_idx, key="apt_revisao_sel")
    revisao     = revisoes[rev_labels.index(rev_sel)]
    revisao_id  = revisao["id"]

    data_inicio   = None
    semanas_total = None
    try:
        if revisao.get("data_inicio"):
            data_inicio = date.fromisoformat(revisao["data_inicio"])
        semanas_total = int(revisao.get("semanas_total") or 0) or None
    except Exception:
        pass

    semana_default = week_from_revisao(_now_brt().date(), data_inicio, semanas_total)

    # Grupo
    grupos = _load_grupos(tenant_id, ver)
    if not grupos:
        empty_state(
            icon="⊕", title="Nenhum grupo cadastrado",
            description="Cadastre grupos de equipamentos para organizar as tarefas.",
            action_label="Ir para Grupos", action_key="apt_goto_grupos",
            nav_to="Admin - Grupos",
        )
        return None, None, None

    grupo_map   = {g["nome"]: g["id"] for g in grupos}
    # Sincroniza com query param se disponível
    qp_grupo = st.query_params.get("grupo")
    default_grupo = qp_grupo if qp_grupo in grupo_map.values() else None
    grupo_names   = list(grupo_map.keys())
    default_name  = next((n for n, gid in grupo_map.items() if gid == default_grupo), grupo_names[0])
    grupo_nome    = st.selectbox("Grupo", grupo_names,
                                  index=grupo_names.index(default_name),
                                  key="apt_grupo_sel")
    grupo_id      = grupo_map[grupo_nome]
    st.query_params["grupo"] = grupo_id  # sincroniza URL

    # Equipamento
    equips = _load_equipamentos(tenant_id, grupo_id, ver)
    if not equips:
        st.info("Nenhum equipamento neste grupo.")
        return None, None, None

    eq_map    = {f"{e['frota']} — {e.get('modelo') or ''}".strip(): e["id"] for e in equips}
    qp_eq     = st.query_params.get("eq")
    eq_names  = list(eq_map.keys())
    default_eq = next((n for n, eid in eq_map.items() if eid == qp_eq), eq_names[0])
    eq_label   = st.selectbox("Equipamento", eq_names,
                               index=eq_names.index(default_eq),
                               key="apt_eq_sel")
    equipamento_id = eq_map[eq_label]
    st.query_params["eq"] = equipamento_id

    st.session_state["_apt_semana_default"] = int(semana_default)
    st.session_state["_apt_revisao_id"]     = revisao_id
    st.session_state["_apt_equipamento_id"] = equipamento_id

    return revisao, revisao_id, equipamento_id


# ── Fragment: editor de tarefas ───────────────────────────────────────────────

@st.fragment
def _fragment_editor(tenant_id: str, revisao_id: str, equipamento_id: str) -> None:
    """Editor de tarefas em fragment — reroda independentemente dos seletores."""
    ver = str(st.session_state.get("data_version", "0"))

    with st.spinner("", show_time=False):
        tarefas = _load_tarefas(tenant_id, revisao_id, equipamento_id, ver)

    if not tarefas:
        st.warning("Nenhuma tarefa encontrada para este equipamento nesta revisão. "
                   "Peça ao Admin para gerar/sincronizar a matriz.")
        return

    semana_default = st.session_state.get("_apt_semana_default", 1)
    user_id        = current_user_id()

    # Filtros rápidos
    col_f1, col_f2 = st.columns([0.6, 0.4])
    with col_f1:
        show_pending = st.toggle("Somente pendentes/travados", value=False, key="apt_pending_toggle")
    with col_f2:
        semana_val = st.number_input("Semana (sugestão)", min_value=0,
                                      value=semana_default, step=1, key="apt_semana_num")

    # Agrupa por setor para filtro de setores
    setores_disponiveis = sorted({
        (((t.get("servicos") or {}).get("setores") or {}).get("nome") or "Setor")
        for t in tarefas
    })
    setor_filtro = st.pills(
        "Filtrar por setor",
        setores_disponiveis,
        selection_mode="multi",
        default=None,
        key="apt_setor_pills",
        label_visibility="collapsed" if len(setores_disponiveis) <= 1 else "visible",
    ) if len(setores_disponiveis) > 1 else None

    # Filtra tarefas
    tarefas_filtradas = tarefas
    if show_pending:
        tarefas_filtradas = [t for t in tarefas_filtradas
                              if t.get("status") in ("pendente", "travado", "em_andamento")]
    if setor_filtro:
        tarefas_filtradas = [
            t for t in tarefas_filtradas
            if (((t.get("servicos") or {}).get("setores") or {}).get("nome") or "Setor") in setor_filtro
        ]

    if not tarefas_filtradas:
        st.info("Nenhuma tarefa para os filtros selecionados.")
        return

    # Monta DataFrame para o editor
    df_orig   = _build_editor_df(tarefas_filtradas, int(semana_val))
    df_display = df_orig.drop(columns=["_id", "_status"])

    # Métricas rápidas antes do editor
    m1, m2, m3, m4 = st.columns(4)
    with m1: st.metric("Total",       len(tarefas_filtradas))
    with m2: st.metric("Concluídos",  sum(1 for t in tarefas_filtradas if t.get("status") == "concluido"))
    with m3: st.metric("Pendentes",   sum(1 for t in tarefas_filtradas if t.get("status") == "pendente"))
    with m4: st.metric("Travados",    sum(1 for t in tarefas_filtradas if t.get("status") == "travado"),
                       delta_color="inverse" if sum(1 for t in tarefas_filtradas if t.get("status") == "travado") > 0 else "off")

    # Barra de progresso do equipamento atual
    _total_tasks = len(tarefas_filtradas)
    _done_tasks  = sum(1 for t in tarefas_filtradas if t.get("status") == "concluido")
    st.progress(
        _done_tasks / _total_tasks if _total_tasks else 0,
        text=f"Progresso: {_done_tasks}/{_total_tasks} tarefas concluídas "
             f"({100 * _done_tasks // _total_tasks if _total_tasks else 0}%)",
    )

    # ── st.data_editor com CheckboxColumn para D, R, M ───────────────────────
    st.caption("Marque as etapas D (Desmontagem), R (Revisão), M (Montagem). "
               "O status é calculado automaticamente: D+R+M = Concluído.")

    edited = st.data_editor(
        df_display,
        column_config={
            "Setor":      st.column_config.TextColumn("Setor",    disabled=True),
            "Serviço":    st.column_config.TextColumn("Serviço",  disabled=True),
            "D":          st.column_config.CheckboxColumn("D",    help="Desmontagem concluída"),
            "R":          st.column_config.CheckboxColumn("R",    help="Revisão concluída"),
            "M":          st.column_config.CheckboxColumn("M",    help="Montagem concluída"),
            "Semana":     st.column_config.NumberColumn("Semana", min_value=0, step=1),
            "Observação": st.column_config.TextColumn("Observação", max_chars=500),
        },
        hide_index=True,
        use_container_width=True,
        num_rows="fixed",
        key="apt_data_editor",
    )

    # Reconstrói df_orig com _id e _status para comparar
    df_edited_full = df_orig.copy()
    df_edited_full[["Setor", "Serviço", "D", "R", "M", "Semana", "Observação"]] = edited[["Setor", "Serviço", "D", "R", "M", "Semana", "Observação"]]

    changes = _df_to_changes(df_edited_full, df_orig, user_id)

    if not changes:
        st.info("Nenhuma alteracao detectada.")
        return

    # Validacao: travado exige observacao
    invalidos = [c for c in changes
                 if c["status"] == "travado" and not (c.get("observacao") or "").strip()]
    if invalidos:
        n = len(invalidos)
        st.error(
            f"{'Um item' if n == 1 else f'{n} itens'} "
            "marcado(s) como Travado sem observacao. Preencha o campo antes de salvar."
        )
        st.stop()

    st.metric(
        "Alteracoes pendentes",
        len(changes),
        delta=f"{'item' if len(changes) == 1 else 'itens'} a salvar",
    )

    # Exportar tarefas filtradas como XLSX
    _exp_df = df_display.copy()
    _col_save, _col_xlsx = st.columns([0.75, 0.25])
    with _col_xlsx:
        try:
            st.download_button(
                "Exportar XLSX",
                icon=":material/download:",
                data=df_to_xlsx(_exp_df, sheet_name="Apontamento"),
                file_name="apontamento.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="apt_xlsx_btn",
            )
        except Exception:
            pass  # openpyxl nao disponivel

    with _col_save:
        if st.button(
            "Salvar alteracoes",
            icon=":material/save:",
            type="primary",
            use_container_width=True,
            key="apt_save_btn",
        ):
            st.session_state["_apt_confirm_save"] = True

    # Dialogo de confirmacao
    n_changes = len(changes)
    confirmed = confirm_dialog(
        trigger_key="_apt_confirm_save",
        title="Salvar alteracoes?",
        body=f"Voce esta prestes a salvar **{n_changes} {'alteracao' if n_changes == 1 else 'alteracoes'}**. Confirma?",
        confirm_label="Salvar",
    )
    if confirmed:
        import time as _time
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

        st.cache_data.clear()
        st.session_state["data_version"] = str(_time.time())
        if erros == 0:
            st.toast(
                f"✅ {n_changes} {'alteração salva' if n_changes == 1 else 'alterações salvas'}.",
                icon=":material/check_circle:",
            )
        else:
            st.toast(f"⚠️ {erros} erro(s) ao salvar.", icon=":material/error:")
        st.rerun()


# ── Ponto de entrada público ──────────────────────────────────────────────────

def render_apontamento() -> None:
    _ph("◉", "Apontamento", "Registre o status de cada tarefa por equipamento, serviço e semana.")

    tenant_id = current_tenant_id()
    ver       = str(st.session_state.get("data_version", "0"))
    revisoes  = _load_revisoes(tenant_id, ver)

    # Fragment 1: seletores (reroda apenas ao mudar revisão/grupo/equipamento)
    _fragment_seletores(revisoes)

    revisao_id     = st.session_state.get("_apt_revisao_id")
    equipamento_id = st.session_state.get("_apt_equipamento_id")

    if not revisao_id or not equipamento_id:
        return

    st.divider()

    # Fragment 2: editor (reroda apenas ao editar tarefas)
    _fragment_editor(tenant_id, revisao_id, equipamento_id)
