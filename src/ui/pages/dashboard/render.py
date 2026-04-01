"""Dashboard — camada de renderização.

Responsabilidade: exibir KPIs, progresso por grupo/setor/equipamento,
heatmap de risco e tendência semanal, tudo a partir dos dados
calculados em transforms.py.
"""
from __future__ import annotations

import time

import pandas as pd
import plotly.express as px
import streamlit as st

from src.auth.scope import get_my_scope
from src.auth.permissions import can_view_all_data
from src.domain.kpi import calc_global_kpis, calc_dept_kpis
from src.ui.core.empty_state import empty_state
from src.ui.components.filters import multiselect_departamentos, multiselect_grupos
from src.ui.components.feedback import notice_card, selection_summary
from src.ui.components.actions import refresh_button
from src.ui.components.tables import data_table
from src.ui.components.states import empty_message, loading_block
from src.ui.core.styles import page_header
from src.ui.core.cache import bump_data_version
from src.utils.kpi_engine import get_group_kpis
from src.utils.nav import get_current_revisao, set_current_revisao
from src.utils.supabase_helpers import current_tenant_id, sb_for_user
from src.db.supabase_client import get_supabase_anon
from src.utils.observability import log_error


def _sb_from_token(token: str = ""):
    sb = get_supabase_anon()
    if token:
        sb.postgrest.auth(token)
    return sb

from src.utils.ui_helpers import status_badge

from .transforms import (
    apply_filters,
    build_inteligencia,
    equipment_progress,
    fmt_date,
    group_progress,
    normalize_matriz_base,
    overall_from_base,
    tendencia_alertas,
)


def _load_revisao(sb, tenant_id: str, revisao_id: str | None = None) -> dict | None:
    rows = (
        sb.table("revisoes")
        .select("id,titulo,status,data_inicio,data_fim,semanas_total")
        .eq("tenant_id", tenant_id)
        .order("created_at", desc=True)
        .limit(50)
        .execute()
        .data
    ) or []

    revisao_id = str(revisao_id or "").strip()
    if revisao_id:
        match = next((r for r in rows if str(r.get("id")) == revisao_id), None)
        if match:
            return match

    ativa = next((r for r in rows if str(r.get("status", "")).lower() == "ativa"), None)
    return ativa or (rows[0] if rows else None)


@st.cache_data(ttl=30, show_spinner=False)
def _load_base_cached(tenant_id: str, revisao_id: str, _token: str = "",
                      ver: str = "0") -> tuple[list, list]:
    # IMPORTANTE: _token tem underscore (excluído do cache key pelo Streamlit).
    # Para evitar que uma chamada inicial com token vazio cache eq_rows=[] e
    # contamine chamadas posteriores com token válido, incluímos um hash do
    # token no ver. Isso garante que o cache é invalidado quando o token muda.
    import hashlib as _hl
    _tok_hash = _hl.md5((_token or "").encode()).hexdigest()[:8]
    ver = f"{ver}_{_tok_hash}"
    sb = _sb_from_token(_token)

    def _fetch_all(query, page_size: int = 1000):
        rows = []
        start = 0
        while True:
            chunk = query.range(start, start + page_size - 1).execute().data or []
            rows.extend(chunk)
            if len(chunk) < page_size:
                break
            start += page_size
        return rows

    try:
        task_rows = _fetch_all(
            sb.table("tarefas_servico")
            .select("equipamento_id,servico_id,status,etapa_d,etapa_r,etapa_m,updated_at")
            .eq("tenant_id", tenant_id)
            .eq("revisao_id", revisao_id)
        )
    except Exception as exc:
        log_error(exc, context="dashboard._load_base_cached", table="tarefas_servico")
        task_rows = []

    # Busca equipamentos via RPC (SECURITY DEFINER) para contornar RLS restritivo.
    # Fallback progressivo: rpc -> table ativo=true -> table all -> IN por tarefa IDs.
    eq_rows = []
    try:
        rpc_result = sb.rpc(
            "get_equipamentos_dashboard",
            {"p_tenant_id": tenant_id}
        ).execute()
        eq_rows = rpc_result.data or []
    except Exception:
        pass

    # Fallbacks sem departamento_id (coluna não existe em equipamentos;
    # vem do equip_grupos via JOIN na função RPC acima).
    if not eq_rows:
        try:
            eq_rows = _fetch_all(
                sb.table("equipamentos")
                .select("id,frota,modelo,grupo_id")
                .eq("tenant_id", tenant_id)
                .eq("ativo", True)
            )
        except Exception as exc:
            log_error(exc, context="dashboard._load_base_cached", table="equipamentos")

    if not eq_rows:
        try:
            eq_rows = _fetch_all(
                sb.table("equipamentos")
                .select("id,frota,modelo,grupo_id")
                .eq("tenant_id", tenant_id)
            )
        except Exception as exc:
            log_error(exc, context="dashboard._load_base_cached.no_ativo", table="equipamentos")

    if not eq_rows and task_rows:
        eq_ids = list({str(t["equipamento_id"]) for t in task_rows if t.get("equipamento_id")})
        for i in range(0, len(eq_ids), 100):
            batch = eq_ids[i:i + 100]
            try:
                chunk = (
                    sb.table("equipamentos")
                    .select("id,frota,modelo,grupo_id")
                    .in_("id", batch)
                    .execute()
                    .data or []
                )
                eq_rows.extend(chunk)
            except Exception as exc:
                log_error(exc, context="dashboard._load_base_cached.fallback_in",
                          table="equipamentos")

    # Busca TODOS os grupos (sem filtro ativo) para garantir resolução de
    # grupo_nome e departamento_id de equipamentos vinculados a grupos inativos.
    # 95 equipamentos apontam para grupos com ativo=False neste tenant.
    try:
        grupo_rows = _fetch_all(
            sb.table("equip_grupos")
            .select("id,nome,departamento_id")
            .eq("tenant_id", tenant_id)
        )
    except Exception as exc:
        log_error(exc, context="dashboard._load_base_cached", table="equip_grupos")
        grupo_rows = []

    serv_rows = []
    try:
        serv_rows = _fetch_all(
            sb.table("servicos")
            .select("id,nome,setor")
            .eq("tenant_id", tenant_id)
        )
    except Exception as exc:
        log_error(exc, context="dashboard._load_base_cached", table="servicos")

    grupo_servicos_rows = []
    try:
        grupo_servicos_rows = _fetch_all(
            sb.table("grupo_servicos")
            .select("grupo_id,servico_id")
            .eq("tenant_id", tenant_id)
        )
    except Exception as exc:
        log_error(exc, context="dashboard._load_base_cached", table="grupo_servicos")

    eq_map = {str(r.get("id")): r for r in eq_rows if r.get("id") is not None}
    grupo_map = {str(r.get("id")): r for r in grupo_rows if r.get("id") is not None}
    serv_map = {str(r.get("id")): r for r in serv_rows if r.get("id") is not None}

    def _status_rank(status: str | None) -> int:
        s = str(status or "").strip().lower()
        order = {
            "concluido": 4,
            "concluído": 4,
            "em_andamento": 3,
            "em andamento": 3,
            "andamento": 3,
            "travado": 2,
            "pendente": 1,
            "nao_aplica": 0,
            "não aplica": 0,
            "nao aplica": 0,
        }
        return order.get(s, -1)

    def _merge_task(prev: dict | None, cur: dict) -> dict:
        if not prev:
            return dict(cur)
        merged = dict(prev)
        for etapa_col in ("etapa_d", "etapa_r", "etapa_m"):
            merged[etapa_col] = bool(prev.get(etapa_col)) or bool(cur.get(etapa_col))
        prev_status = prev.get("status")
        cur_status = cur.get("status")
        merged["status"] = cur_status if _status_rank(cur_status) >= _status_rank(prev_status) else prev_status
        prev_upd = str(prev.get("updated_at") or "")
        cur_upd = str(cur.get("updated_at") or "")
        merged["updated_at"] = cur.get("updated_at") if cur_upd >= prev_upd else prev.get("updated_at")
        return merged

    raw_tasks = []
    task_map: dict[tuple[str, str], dict] = {}
    for t in task_rows:
        eid = str(t.get("equipamento_id")) if t.get("equipamento_id") is not None else None
        sid = str(t.get("servico_id")) if t.get("servico_id") is not None else None
        eq = eq_map.get(eid, {})
        gid = eq.get("grupo_id")
        gid_s = str(gid) if gid is not None else None
        grp = grupo_map.get(gid_s, {})
        svc = serv_map.get(sid, {})
        raw_tasks.append({
            "equipamento_id": t.get("equipamento_id"),
            "grupo_id": gid,
            "grupo_nome": grp.get("nome"),
            "departamento_id": eq.get("departamento_id") or grp.get("departamento_id"),
            "frota": eq.get("frota"),
            "modelo": eq.get("modelo"),
            "servico_id": t.get("servico_id"),
            "setor_nome": svc.get("setor") or "—",
            "status": t.get("status"),
            "etapa_d": t.get("etapa_d"),
            "etapa_r": t.get("etapa_r"),
            "etapa_m": t.get("etapa_m"),
            "updated_at": t.get("updated_at"),
        })
        if eid and sid and eid in eq_map:
            task_map[(eid, sid)] = _merge_task(task_map.get((eid, sid)), t)

    group_services: dict[str, list[str]] = {}
    for row in grupo_servicos_rows:
        gid = row.get("grupo_id")
        sid = row.get("servico_id")
        if gid is None or sid is None:
            continue
        gid_s = str(gid)
        sid_s = str(sid)
        group_services.setdefault(gid_s, [])
        if sid_s not in group_services[gid_s]:
            group_services[gid_s].append(sid_s)

    # IDs de equipamentos que já possuem vínculo via grupo_servicos
    eids_covered: set[str] = set()

    raw = []
    for eid, eq in eq_map.items():
        gid = eq.get("grupo_id")
        gid_s = str(gid) if gid is not None else None
        if not gid_s:
            continue
        grp = grupo_map.get(gid_s, {})
        service_ids = group_services.get(gid_s, [])
        if not service_ids:
            # Sem vínculo em grupo_servicos: equipamento será coberto pelo
            # fallback por tarefa direta abaixo, se houver tarefas.
            continue
        eids_covered.add(eid)
        for sid in service_ids:
            svc = serv_map.get(str(sid), {})
            t = task_map.get((eid, str(sid)), {})
            raw.append({
                "equipamento_id": eq.get("id"),
                "grupo_id": gid,
                "grupo_nome": grp.get("nome"),
                "departamento_id": eq.get("departamento_id") or grp.get("departamento_id"),
                "frota": eq.get("frota"),
                "modelo": eq.get("modelo"),
                "servico_id": sid,
                "setor_nome": svc.get("setor") or "—",
                "status": t.get("status") or "pendente",
                "etapa_d": t.get("etapa_d"),
                "etapa_r": t.get("etapa_r"),
                "etapa_m": t.get("etapa_m"),
                "updated_at": t.get("updated_at"),
            })

    # Fallback granular: para equipamentos sem cobertura via grupo_servicos
    # (grupo sem serviços vinculados), usa as tarefas diretas da revisão.
    # Isso evita que equipamentos com movimentações desapareçam do dashboard
    # quando a tabela grupo_servicos estiver desatualizada ou incompleta.
    fallback_tasks = [t for t in raw_tasks if str(t.get("equipamento_id") or "") not in eids_covered]
    if fallback_tasks:
        raw.extend(fallback_tasks)

    # Fallback total: se nenhuma grade pôde ser montada, usa todas as tarefas.
    if not raw and raw_tasks:
        raw = raw_tasks

    eq_meta = [
        {
            "equipamento_id": r.get("id"),
            "frota": r.get("frota"),
            "modelo": r.get("modelo"),
            "departamento_id": r.get("departamento_id"),
        }
        for r in eq_rows
    ]
    return raw, eq_meta


@st.cache_data(ttl=120, show_spinner=False)
def _load_departamentos(tenant_id: str, ver: str = "0", _token: str = "") -> list[dict]:
    import hashlib as _hl
    ver = f"{ver}_{_hl.md5((_token or '').encode()).hexdigest()[:8]}"
    sb = _sb_from_token(_token)
    try:
        return (
            sb.table("departamentos")
            .select("id,nome")
            .eq("tenant_id", tenant_id)
            .eq("ativo", True)
            .order("nome")
            .execute()
            .data or []
        )
    except Exception as exc:
        log_error(exc, context="dashboard._load_departamentos", table="departamentos")
        return []


@st.cache_data(ttl=120, show_spinner=False)
def _load_grupos(tenant_id: str, ver: str = "0", _token: str = "") -> list[dict]:
    import hashlib as _hl
    ver = f"{ver}_{_hl.md5((_token or '').encode()).hexdigest()[:8]}"
    sb = _sb_from_token(_token)
    try:
        return (
            sb.table("equip_grupos")
            .select("id,nome,departamento_id")
            .eq("tenant_id", tenant_id)
            .eq("ativo", True)
            .order("nome")
            .execute()
            .data or []
        )
    except Exception as exc:
        log_error(exc, context="dashboard._load_grupos", table="equip_grupos")
        return []


def _pct_bar_color(v: float) -> str:
    """Cor condicional padrão: verde >= 80 | amarelo >= 50 | vermelho < 50."""
    if v >= 80:
        return "#12B76A"
    if v >= 50:
        return "#F59E0B"
    return "#EF4444"




def _equipment_progress_robusto(base: pd.DataFrame) -> pd.DataFrame:
    """Fallback robusto para equipamentos quando equipment_progress vier zerado.

    Prioriza a função original em transforms.py, mas quando todos os percentuais
    retornam 0 apesar de existir movimentação/status, recompõe o progresso por
    equipamento diretamente da base filtrada.
    """
    # Primeiro tenta a implementação oficial de transforms.py.
    # A versão anterior chamava esta própria função recursivamente,
    # o que fazia a aba de equipamentos quebrar/zerar.
    try:
        edf = equipment_progress(base)
    except Exception:
        edf = pd.DataFrame()

    if edf is None or edf.empty:
        edf = pd.DataFrame()

    def _all_zero(df: pd.DataFrame) -> bool:
        if df is None or df.empty or "% Concluído" not in df.columns:
            return True
        vals = pd.to_numeric(df["% Concluído"], errors="coerce").fillna(0)
        return float(vals.max()) <= 0

    if not _all_zero(edf):
        return edf

    if base is None or base.empty or "equipamento_id" not in base.columns:
        return edf

    tmp = base.copy()
    status_col = tmp["status"].astype(str).str.strip().str.lower() if "status" in tmp.columns else pd.Series("", index=tmp.index)
    for c in ("etapa_d", "etapa_r", "etapa_m"):
        if c not in tmp.columns:
            tmp[c] = False
        tmp[c] = tmp[c].fillna(False).astype(bool)

    tmp["_pend"] = status_col.eq("pendente")
    tmp["_and"] = status_col.isin(["em_andamento", "em andamento", "andamento"])
    tmp["_trav"] = status_col.eq("travado")
    tmp["_na"] = status_col.isin(["nao_aplica", "não aplica", "nao aplica"])
    tmp["_concl"] = status_col.isin(["concluido", "concluído"])
    tmp["_done_steps"] = tmp[["etapa_d", "etapa_r", "etapa_m"]].sum(axis=1)
    tmp["_expected_steps"] = 3

    keys = ["equipamento_id"]
    for extra in ("frota", "modelo", "departamento_id"):
        if extra in tmp.columns:
            keys.append(extra)

    agg = (
        tmp.groupby(keys, dropna=False)
        .agg(
            Total=("equipamento_id", "size"),
            Pendentes=("_pend", "sum"),
            **{"Em andamento": ("_and", "sum")},
            Travados=("_trav", "sum"),
            **{"Não aplica": ("_na", "sum")},
            **{"Concluídos": ("_concl", "sum")},
            _done_steps=("_done_steps", "sum"),
            _expected_steps=("_expected_steps", "sum"),
        )
        .reset_index()
    )

    agg.rename(columns={"frota": "Frota", "modelo": "Modelo"}, inplace=True)

    pct_steps = (agg["_done_steps"] / agg["_expected_steps"].replace(0, pd.NA) * 100).fillna(0)
    pct_status = ((agg["Concluídos"] + (agg["Em andamento"] * 0.5)) / agg["Total"].replace(0, pd.NA) * 100).fillna(0)
    agg["% Concluído"] = pd.concat([pct_steps, pct_status], axis=1).max(axis=1).round(0).clip(0, 100)

    for col in ["Frota", "Modelo"]:
        if col not in agg.columns:
            agg[col] = "—"

    keep = [
        "equipamento_id", "Frota", "Modelo", "departamento_id", "Total",
        "% Concluído", "Pendentes", "Em andamento", "Travados", "Não aplica", "Concluídos",
    ]
    return agg[[c for c in keep if c in agg.columns]].copy()


def _render_pct_rank_chart(
        df: pd.DataFrame,
        category_col: str,
        value_col: str,
        title: str,
        top_n: int = 10) -> None:
    chart_df = df.copy()
    if chart_df.empty or category_col not in chart_df.columns or value_col not in chart_df.columns:
        st.info("Sem dados para exibir.")
        return

    chart_df = chart_df[[category_col, value_col]].copy()
    chart_df[category_col] = chart_df[category_col].fillna("—").astype(str)
    chart_df[value_col] = pd.to_numeric(
        chart_df[value_col], errors="coerce").fillna(0).clip(0, 100)
    chart_df = chart_df.sort_values(value_col, ascending=False).head(top_n)
    chart_df = chart_df.sort_values(value_col, ascending=True)
    chart_df["label"] = chart_df[value_col].map(lambda v: f"{int(round(v))}%")
    chart_df["color"] = chart_df[value_col].apply(_pct_bar_color)

    fig = px.bar(
        chart_df,
        x=value_col,
        y=category_col,
        orientation="h",
        text="label",
        color="color",
        color_discrete_map="identity",
        title=title,
    )
    fig.update_traces(
        textposition="outside",
        cliponaxis=False,
        hovertemplate="%{y}<br>% Concluído: %{x:.0f}%<extra></extra>")
    fig.update_layout(
        height=max(380, 42 * len(chart_df) + 80),
        margin=dict(l=10, r=90, t=48, b=10),
        xaxis=dict(range=[0, 110], title="% Concluído"),
        yaxis=dict(title="", type="category"),
        paper_bgcolor="#06080B",
        plot_bgcolor="#0C111A",
        font=dict(color="#E8EDF5", family="DM Sans, sans-serif", size=11),
        showlegend=False,
    )
    st.plotly_chart(
        fig, use_container_width=True, config={"displayModeBar": False})


@st.fragment
def _fragment_kpis_globais(overall: dict) -> None:
    from src.utils.ui_helpers import mobile_columns
    cols = mobile_columns(5, 2)
    labels = [("% Concluído",
               f"{int(round(overall.get('pct', 0)))}%",
               None,
               "off",
               "Percentual global alinhado à mesma regra da Matriz/Home."),
              ("Concluídos",
               overall.get("concl", 0),
               None,
               "off",
               None),
              ("Em andamento",
               overall.get("andamento", 0),
               None,
               "off",
               None),
              ("Sem início",
               overall.get("sem_inicio", overall.get("pend", 0)),
               None,
               "off",
               None),
              ("Em atraso",
               overall.get("atrasados", overall.get("trav", 0)),
               None,
               "off",
               None),
              ]
    for i, (label, value, delta, delta_color, help_text) in enumerate(labels):
        with cols[i % len(cols)]:
            st.metric(
                label,
                value,
                delta=delta,
                delta_color=delta_color,
                help=help_text)


@st.fragment
def _fragment_previsao(previsao: dict, risco: dict) -> None:
    _RISCO_ICONS = {"baixo": "🟢", "medio": "🟡", "alto": "🔴"}
    _PREV_LABELS = {
        "no_prazo": "No prazo",
        "atraso": "Em atraso",
        "vence_hoje": "Vence hoje",
        "concluido": "Concluído",
        "sem_prazo": "Sem prazo",
        "sem_base": "Sem base"}
    status_prev = previsao.get("status_previsao", "sem_base")
    status_risco = risco.get("status_risco", "baixo")

    c1, c2 = st.columns(2)
    with c1:
        prazo = previsao.get("data_fim_planejada")
        st.metric(
            "Prazo da revisão",
            fmt_date(prazo) if prazo is not None else "—")
        st.caption(
            f"Status: **{_PREV_LABELS.get(status_prev, status_prev)}**")
    with c2:
        icon = _RISCO_ICONS.get(status_risco, "⚪")
        st.metric(
            f"{icon} Risco operacional",
            f"{risco.get('risco_score', 0):.1f}",
            help="Score: travados × 3 + pendentes × 1.5 + em_andamento × 1.",
        )
        st.caption(
            f"Ritmo necessário: **{previsao.get('ritmo_medio_dia', 0):.2f}%/dia** | "
            f"Dias passados: **{previsao.get('dias_passados', 0)}** | "
            f"Dias restantes: **{previsao.get('dias_restantes_estimados', 0):.0f}**"
        )


@st.fragment
def _fragment_grupos(
        base: pd.DataFrame,
        dept_map: dict,
        group_kpis_df: pd.DataFrame | None = None,
        top_n: int = 10) -> None:
    gdf = group_kpis_df.copy(
    ) if group_kpis_df is not None and not group_kpis_df.empty else group_progress(base)
    if gdf.empty:
        empty_state(icon="⊕", title="Sem dados de grupos",
                    description="Nenhum grupo com tarefas para esta revisão.")
        return
    gdf = gdf.copy()
    gdf["Departamento"] = gdf["departamento_id"].map(lambda v: dept_map.get(str(v)) if pd.notna(v) else None).fillna("—")
    gdf["pct_concluido"] = pd.to_numeric(
        gdf["pct_concluido"],
        errors="coerce").fillna(0).clip(
        0,
        100)
    display = (
        gdf[["grupo", "Departamento", "pct_concluido"]]
        .rename(columns={"grupo": "Grupo", "pct_concluido": "% Concluído"})
        .sort_values(["% Concluído", "Grupo"], ascending=[False, True])
    )
    _render_pct_rank_chart(
        display,
        "Grupo",
        "% Concluído",
        f"Top {top_n} grupos por % de conclusão",
        top_n=top_n)
    data_table(
        display.head(top_n),
        column_config={
            "% Concluído": st.column_config.ProgressColumn(
                "% Concluído",
                min_value=0,
                max_value=100,
                format="%d%%")},
    )
    with st.expander("⬇ Exportar", expanded=False):
        from src.utils.ui_helpers import df_to_xlsx
        _exp = display.copy()
        col_csv, col_xlsx = st.columns(2)
        with col_csv:
            st.download_button(
                "CSV", _exp.to_csv(index=False).encode("utf-8"),
                file_name="dashboard_grupos.csv", mime="text/csv",
                use_container_width=True, key="dash_grupos_csv",
            )
        with col_xlsx:
            st.download_button(
                "XLSX",
                df_to_xlsx(
                    _exp,
                    sheet_name="Grupos"),
                file_name="dashboard_grupos.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="dash_grupos_xlsx",
            )


@st.fragment
def _fragment_departamentos(
        group_kpis_df: pd.DataFrame,
        gid_to_dept: dict,
        dept_map: dict) -> None:
    """Gráfico de barras horizontais: progresso por departamento.

    Clique em uma barra para filtrar o dashboard por aquele departamento.
    Usa on_select do Plotly — zero queries extras, apenas session_state + rerun.
    """
    if group_kpis_df is None or group_kpis_df.empty:
        empty_message("Sem dados de departamentos.")
        return

    # Agrega por departamento diretamente do formato group_progress()
    # (colunas: grupo_id, departamento_id, done_steps, expected_steps)
    # sem depender de calc_dept_kpis que exige eq_count/svc_count do GroupKPI.
    tmp = group_kpis_df.copy()
    if "departamento_id" not in tmp.columns:
        tmp["departamento_id"] = tmp["grupo_id"].map(lambda v: gid_to_dept.get(str(v)) if pd.notna(v) else None)
    # Normaliza para str para garantir compatibilidade com dept_map (chaves são str(uuid))
    tmp["departamento_id"] = tmp["departamento_id"].map(lambda v: str(v) if pd.notna(v) and v is not None else None)
    tmp = tmp.dropna(subset=["departamento_id"])
    tmp = tmp[pd.to_numeric(tmp.get("expected_steps", 0), errors="coerce").fillna(0) > 0]
    if tmp.empty:
        empty_message("Sem dados de departamentos para esta revisão.")
        return
    dsum = (
        tmp.groupby("departamento_id", dropna=True)
        .agg(
            done_steps=("done_steps", "sum"),
            expected_steps=("expected_steps", "sum"),
            grupos=("grupo_id", "nunique"),
        )
        .reset_index()
    )
    dsum["backlog_steps"] = (dsum["expected_steps"] - dsum["done_steps"]).clip(lower=0)
    dsum["pct"] = (
        (dsum["done_steps"] / dsum["expected_steps"] * 100)
        .round(0)
        .fillna(0.0)
        .clip(0, 100)
    )
    if dsum is None or dsum.empty:
        empty_message("Sem dados de departamentos para esta revisão.")
        return

    dsum = dsum.copy()
    dsum["Departamento"] = dsum["departamento_id"].map(lambda v: dept_map.get(str(v)) if pd.notna(v) else None).fillna("—")
    dsum["pct"] = pd.to_numeric(dsum.get("pct", 0), errors="coerce").fillna(0).clip(0, 100)
    dsum["Grupos"] = dsum.get("grupos", pd.Series(dtype=int)).fillna(0).astype(int)
    dsum["Etapas feitas"] = dsum.get("done_steps", pd.Series(dtype=int)).fillna(0).astype(int)
    dsum["Etapas esperadas"] = dsum.get("expected_steps", pd.Series(dtype=int)).fillna(0).astype(int)

    display = (
        dsum[["Departamento", "pct", "Grupos", "Etapas feitas", "Etapas esperadas"]]
        .rename(columns={"pct": "% Concluído"})
        .sort_values(["% Concluído", "Departamento"], ascending=[False, True])
    )

    chart_df = display[["Departamento", "% Concluído"]].copy()
    chart_df = chart_df.sort_values("% Concluído", ascending=True)
    chart_df["label"] = chart_df["% Concluído"].map(lambda v: f"{int(round(v))}%")
    chart_df["color"] = chart_df["% Concluído"].apply(_pct_bar_color)

    fig = px.bar(
        chart_df,
        x="% Concluído",
        y="Departamento",
        orientation="h",
        text="label",
        color="color",
        color_discrete_map="identity",
        title="Progresso por departamento — clique numa barra para filtrar",
    )
    fig.update_traces(
        textposition="outside",
        cliponaxis=False,
        hovertemplate="%{y}<br>% Concluído: %{x:.0f}%<br><i>Clique para filtrar</i><extra></extra>",
    )
    fig.update_layout(
        height=max(320, 52 * len(chart_df) + 80),
        margin=dict(l=10, r=90, t=52, b=10),
        xaxis=dict(range=[0, 110], title="% Concluído"),
        yaxis=dict(title="", type="category"),
        paper_bgcolor="#06080B",
        plot_bgcolor="#0C111A",
        font=dict(color="#E8EDF5", family="DM Sans, sans-serif", size=12),
        showlegend=False,
    )

    # on_select: Streamlit captura o clique sem rerenderizar nada — zero custo
    event = st.plotly_chart(
        fig,
        use_container_width=True,
        config={"displayModeBar": False},
        on_select="rerun",
        key="dash_dept_chart",
    )

    # Processa clique: aplica filtro e reexecuta sem nenhuma query extra
    # session_state["dash_filter_dept"] armazena NOMES (o que st.multiselect usa internamente)
    if event and event.get("selection", {}).get("points"):
        clicked_name = event["selection"]["points"][0].get("y", "")
        if clicked_name and clicked_name != "—":
            current = st.session_state.get("dash_filter_dept", [])
            if clicked_name in current:
                # segundo clique no mesmo: remove (toggle)
                new_val = [x for x in current if x != clicked_name]
            else:
                new_val = [clicked_name]
            st.session_state["dash_filter_dept"] = new_val
            st.rerun()

    st.caption("💡 Clique em uma barra para filtrar o dashboard por departamento. Clique novamente para limpar.")

    data_table(
        display,
        column_config={
            "% Concluído": st.column_config.ProgressColumn(
                "% Concluído", min_value=0, max_value=100, format="%d%%"),
            "Grupos": st.column_config.NumberColumn("Grupos", format="%d"),
            "Etapas feitas": st.column_config.NumberColumn("Etapas feitas", format="%d"),
            "Etapas esperadas": st.column_config.NumberColumn("Etapas esperadas", format="%d"),
        },
    )

    with st.expander("⬇ Exportar", expanded=False):
        from src.utils.ui_helpers import df_to_xlsx
        col_csv, col_xlsx = st.columns(2)
        with col_csv:
            st.download_button(
                "CSV", display.to_csv(index=False).encode("utf-8"),
                file_name="dashboard_departamentos.csv", mime="text/csv",
                use_container_width=True, key="dash_dept_csv",
            )
        with col_xlsx:
            st.download_button(
                "XLSX",
                df_to_xlsx(display, sheet_name="Departamentos"),
                file_name="dashboard_departamentos.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True, key="dash_dept_xlsx",
            )


@st.fragment
def _fragment_equipamentos(
        base: pd.DataFrame,
        dept_map: dict,
        top_n: int = 10) -> None:
    edf = _equipment_progress_robusto(base)
    if edf.empty:
        st.info("Sem dados de equipamentos.")
        return

    edf = edf.copy()
    edf["Frota"] = edf["Frota"].fillna("—").astype(str).str.strip()
    edf["Modelo"] = edf["Modelo"].fillna("—").astype(str).str.strip()
    edf["Departamento"] = edf["departamento_id"].map(lambda v: dept_map.get(str(v)) if pd.notna(v) else None).fillna("—")

    busca = st.text_input(
        "Buscar frota / modelo",
        placeholder="Ex.: 2055, JD 6190…",
        key="dash_busca_eq")
    if busca.strip():
        termo = busca.strip().lower()
        mask = (
            edf["Frota"].str.lower().str.contains(termo, na=False)
            | edf["Modelo"].str.lower().str.contains(termo, na=False)
        )
        edf = edf[mask]

    if edf.empty:
        st.info("Nenhum equipamento encontrado para o filtro informado.")
        return

    edf["equipamento_id"] = edf["equipamento_id"].map(
        lambda v: str(v).strip() if pd.notna(v) and str(v).strip() else "—"
    )
    edf["Equipamento"] = edf.apply(
        lambda r: (
            f"{r['Frota']} — {r['Modelo']}"
            if str(r["Frota"]).strip() not in {"", "—"} and str(r["Modelo"]).strip() not in {"", "—"}
            else str(r["Frota"]) if str(r["Frota"]).strip() not in {"", "—"}
            else str(r["Modelo"]) if str(r["Modelo"]).strip() not in {"", "—"}
            else f"ID {str(r['equipamento_id'])[:8]}"
        ),
        axis=1,
    )

    rank_df = edf.sort_values(
        ["% Concluído", "Concluídos", "Equipamento"],
        ascending=[False, False, True],
    ).head(top_n)
    _render_pct_rank_chart(
        rank_df,
        "Equipamento",
        "% Concluído",
        f"Top {top_n} equipamentos por % de conclusão",
        top_n=top_n,
    )

    cols = [
        "Equipamento",
        "Frota",
        "Modelo",
        "Departamento",
        "Total",
        "% Concluído",
        "Pendentes",
        "Em andamento",
        "Travados",
        "Não aplica",
        "Concluídos",
    ]
    data_table(
        rank_df[cols],
        column_config={
            "% Concluído": st.column_config.ProgressColumn(
                "% Concluído",
                min_value=0,
                max_value=100,
                format="%d%%",
            )
        },
    )
    with st.expander("⬇ Exportar tabela completa", expanded=False):
        from src.utils.ui_helpers import df_to_xlsx
        _exp = edf[cols].sort_values(
            ["% Concluído", "Equipamento"], ascending=[False, True])
        col_csv, col_xlsx = st.columns(2)
        with col_csv:
            st.download_button(
                "CSV", _exp.to_csv(index=False).encode("utf-8"),
                file_name="dashboard_equipamentos.csv", mime="text/csv",
                use_container_width=True, key="dash_eq_csv",
            )
        with col_xlsx:
            st.download_button(
                "XLSX",
                df_to_xlsx(
                    _exp,
                    sheet_name="Equipamentos"),
                file_name="dashboard_equipamentos.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="dash_eq_xlsx",
            )


@st.fragment
def _fragment_heatmap(heat: pd.DataFrame) -> None:
    if heat.empty:
        empty_message("Sem dados de heatmap para esta revisão.")
        return
    fig = px.density_heatmap(
        heat,
        x="setor",
        y="grupo",
        z="calor_score",
        color_continuous_scale="RdYlGn_r",
        labels={
            "setor": "Setor",
            "grupo": "Grupo",
            "calor_score": "Score de risco"},
        title="Heatmap de Risco — Grupo × Setor",
    )
    fig.update_layout(
        height=max(300, len(heat["grupo"].unique()) * 40 + 80),
        margin=dict(l=10, r=10, t=40, b=10),
        paper_bgcolor="#06080B",
        plot_bgcolor="#0C111A",
        font=dict(color="#E8EDF5", family="DM Sans, sans-serif", size=11),
    )
    st.plotly_chart(
        fig, use_container_width=True, config={
            "displayModeBar": False})
    st.caption(
        "Score: travado × 3 + pendente × 1.5 + em_andamento × 1, normalizado por total.")


@st.fragment
def _fragment_criticidade(crit: pd.DataFrame) -> None:
    if crit.empty:
        empty_message("Sem equipamentos críticos para exibir.")
        return
    cols = [
        "ranking_criticidade",
        "Equipamento",
        "grupo",
        "criticidade_score",
        "travados",
        "pendentes",
        "pct_concluido"]
    present = [c for c in cols if c in crit.columns]
    data_table(
        crit[present].head(20),
        column_config={
            "ranking_criticidade": st.column_config.NumberColumn("#", width="small"),
            "criticidade_score": st.column_config.NumberColumn("Score", format="%.2f"),
            "pct_concluido": st.column_config.ProgressColumn("% Concluído", min_value=0, max_value=100),
        },
    )


@st.fragment
def _fragment_tendencia(trend: pd.DataFrame) -> None:
    if trend.empty:
        empty_message("Sem base suficiente para tendência semanal nesta revisão.")
        return

    trend = trend.copy().sort_values("week_number")
    alert = tendencia_alertas(trend)
    status = alert.get("status", "sem_base")
    tone_map = {
        "acima": "success",
        "atencao": "warning",
        "abaixo": "warning",
        "estagnado": "warning",
        "sem_base": "info",
    }
    title_map = {
        "acima": "Tendência saudável",
        "atencao": "Tendência em atenção",
        "abaixo": "Ritmo abaixo do ideal",
        "estagnado": "Evolução estagnada",
        "sem_base": "Sem base suficiente",
    }
    notice_card(
        title_map.get(status, "Tendência semanal"),
        f"{alert.get('mensagem', 'Sem leitura disponível.')} "
        f"Delta atual: {alert.get('delta_atual', 0):+.1f} p.p. | "
        f"Ganho última semana: {alert.get('ganho_ultima_semana', 0):+.1f} p.p.",
        tone=tone_map.get(status, "info"),
    )

    plot_df = trend.melt(
        id_vars=["week_number", "semana_label"],
        value_vars=["pct_real", "pct_ideal"],
        var_name="serie",
        value_name="pct",
    )
    plot_df["serie"] = plot_df["serie"].map({
        "pct_real": "Real",
        "pct_ideal": "Ideal",
    }).fillna(plot_df["serie"])

    fig = px.line(
        plot_df,
        x="semana_label",
        y="pct",
        color="serie",
        markers=True,
        line_dash="serie",
        category_orders={"serie": ["Real", "Ideal"]},
        title="Evolução semanal da revisão",
        color_discrete_map={"Real": "#22C55E", "Ideal": "#94A3B8"},
        line_dash_map={"Real": "solid", "Ideal": "dash"},
    )
    fig.update_traces(hovertemplate="%{x}<br>%{fullData.name}: %{y:.1f}%<extra></extra>")
    fig.update_layout(
        height=360,
        margin=dict(l=10, r=10, t=48, b=10),
        xaxis_title="Semana",
        yaxis_title="% Concluído",
        yaxis=dict(range=[0, 100]),
        paper_bgcolor="#06080B",
        plot_bgcolor="#0C111A",
        font=dict(color="#E8EDF5", family="DM Sans, sans-serif", size=11),
        legend_title_text="",
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    display = trend[["semana_label", "pct_real", "pct_ideal", "delta_pct"]].copy()
    display = display.rename(columns={
        "semana_label": "Semana",
        "pct_real": "Real (%)",
        "pct_ideal": "Ideal (%)",
        "delta_pct": "Delta (p.p.)",
    })
    data_table(
        display.sort_values("Semana", ascending=False),
        column_config={
            "Real (%)": st.column_config.NumberColumn("Real (%)", format="%.1f"),
            "Ideal (%)": st.column_config.NumberColumn("Ideal (%)", format="%.1f"),
            "Delta (p.p.)": st.column_config.NumberColumn("Delta (p.p.)", format="%.1f"),
        },
    )




def _groups_from_kpi_df(kdf: pd.DataFrame, gid_to_name: dict, gid_to_dept: dict) -> pd.DataFrame:
    if kdf is None or kdf.empty:
        return pd.DataFrame(columns=[
            "grupo", "grupo_id", "departamento_id", "pct_concluido", "done_steps", "expected_steps"
        ])
    tmp = kdf.copy()
    tmp["grupo_id"] = tmp["grupo_id"].map(lambda v: str(v) if pd.notna(v) else None)
    tmp["departamento_id"] = tmp["grupo_id"].map(lambda v: gid_to_dept.get(str(v)) if v is not None else None)
    tmp["grupo"] = tmp["grupo_id"].map(lambda v: gid_to_name.get(str(v), str(v)) if v is not None else "—")
    tmp["done_steps"] = pd.to_numeric(tmp.get("done_steps", 0), errors="coerce").fillna(0).astype(int)
    tmp["expected_steps"] = pd.to_numeric(tmp.get("expected_steps", 0), errors="coerce").fillna(0).astype(int)
    if "pct" in tmp.columns:
        tmp["pct_concluido"] = pd.to_numeric(tmp["pct"], errors="coerce").fillna(0).clip(0, 100)
    else:
        tmp["pct_concluido"] = (tmp["done_steps"] / tmp["expected_steps"].replace(0, pd.NA) * 100).fillna(0).clip(0, 100)
    return tmp[["grupo", "grupo_id", "departamento_id", "pct_concluido", "done_steps", "expected_steps"]].copy()


def _overall_from_group_kpis(kdf: pd.DataFrame) -> dict:
    gk = calc_global_kpis(kdf)
    return {
        "pct": float(gk.get("pct", 0) or 0),
        "total": int(len(kdf)) if kdf is not None else 0,
        "concl": 0,
        "sem_inicio": 0,
        "andamento": 0,
        "atrasados": 0,
        "pend": 0,
        "trav": 0,
        "na": 0,
    }

def render_dashboard() -> None:
    page_header("Dashboard")

    tenant_id = current_tenant_id()
    if not tenant_id:
        st.info("Selecione um tenant para ver o dashboard.")
        return

    sb = sb_for_user()
    dep_scope_ids, grp_scope_ids = get_my_scope(tenant_id, sb)
    role = st.session_state.get("current_role") or ""
    if can_view_all_data(role):
        if dep_scope_ids == []:
            dep_scope_ids = None
        if grp_scope_ids == []:
            grp_scope_ids = None
    if not can_view_all_data(role) and dep_scope_ids == [] and grp_scope_ids == []:
        st.warning("Você não possui departamentos ou grupos vinculados para visualizar o dashboard.")
        return

    with st.spinner("", show_time=False):
        rev = _load_revisao(sb, tenant_id, get_current_revisao())
    if not rev:
        st.warning("Nenhuma revisão ativa encontrada para este tenant.")
        return

    revisao_id = str(rev["id"])
    set_current_revisao(revisao_id)
    st.session_state["_sidebar_rev_titulo"] = rev.get("titulo")

    h1, h2 = st.columns([0.82, 0.18])
    with h1:
        st.markdown(f"## {rev.get('titulo', 'Revisão')}")
        status_badge(rev.get("status"))
    with h2:
        if refresh_button("dash_refresh_btn", help="Recarrega os dados consolidados desta revisão."):
            bump_data_version()
            st.toast("Atualizado", icon=":material/refresh:")
            st.rerun()

    selection_summary(
        "Contexto da revisão",
        {
            "Revisão": rev.get("titulo") or "Revisão ativa",
            "Status": rev.get("status") or "-",
            "Período": f"{rev.get('data_inicio') or '-'} -> {rev.get('data_fim') or '-'}",
        },
        caption="Os filtros abaixo refinam apenas a visualização atual do dashboard.",
    )

    ver = str(st.session_state.get("data_version", "0"))
    token = st.session_state.get("sb_access_token", "")
    with st.spinner("", show_time=False):
        raw, eq_meta = _load_base_cached(tenant_id, revisao_id, token, ver)
        departamentos = _load_departamentos(tenant_id, ver, token)
        grupos = _load_grupos(tenant_id, ver, token)

    if dep_scope_ids in (None, [] ) and grp_scope_ids not in (None, []):
        dep_scope_ids = sorted({str(g.get("departamento_id")) for g in grupos if g.get("id") in set(grp_scope_ids) and g.get("departamento_id")})

    if dep_scope_ids == [] and grp_scope_ids not in (None, []):
        grp_set = {str(x) for x in grp_scope_ids}
        departamentos = [d for d in departamentos if any(str(g.get("id")) in grp_set and str(g.get("departamento_id")) == str(d.get("id")) for g in grupos)]

    if dep_scope_ids is not None:
        dep_scope_set = {str(x) for x in dep_scope_ids}
        departamentos = [d for d in departamentos if str(d.get("id")) in dep_scope_set]
    if grp_scope_ids is not None:
        grp_scope_set = {str(x) for x in grp_scope_ids}
        scoped_grupos = [g for g in grupos if str(g.get("id")) in grp_scope_set]
        if scoped_grupos or dep_scope_ids in (None, []):
            grupos = scoped_grupos
        else:
            grupos = [g for g in grupos if g.get("departamento_id") in dep_scope_ids]

    dept_map = {str(d["id"]): d.get("nome", "—")
                for d in departamentos if d.get("id")}
    gid_to_name = {str(g["id"]): g.get("nome", "—") for g in grupos if g.get("id")}
    gid_to_dept = {str(g["id"]): str(g.get("departamento_id")) if g.get("departamento_id") else None
                   for g in grupos if g.get("id")}

    base = normalize_matriz_base(raw, eq_meta)
    if not base.empty:
        if "data_inicio" not in base.columns:
            base["data_inicio"] = pd.NaT
        if "data_fim" not in base.columns:
            base["data_fim"] = pd.NaT
        rev_inicio = pd.to_datetime(rev.get("data_inicio"), errors="coerce")
        rev_fim = pd.to_datetime(rev.get("data_fim"), errors="coerce")
        if pd.notna(rev_inicio):
            base["data_inicio"] = pd.to_datetime(base["data_inicio"], errors="coerce").fillna(rev_inicio)
        if pd.notna(rev_fim):
            base["data_fim"] = pd.to_datetime(base["data_fim"], errors="coerce").fillna(rev_fim)
    raw_base = base.copy()

    # KPI consolidado para fallback de perfis com escopo/RLS mais restritivo.
    kpi_df = get_group_kpis(tenant_id, revisao_id, ver, prefer_mv=True, _token=token)
    if kpi_df is None:
        kpi_df = pd.DataFrame()
    if not kpi_df.empty:
        kpi_df = kpi_df.copy()
        kpi_df["grupo_id"] = kpi_df["grupo_id"].map(lambda v: str(v) if pd.notna(v) else None)
        if dep_scope_ids not in (None, []):
            _dep_scope_set = {str(x) for x in dep_scope_ids}
            kpi_df = kpi_df[kpi_df["grupo_id"].map(lambda v: gid_to_dept.get(str(v)) if v is not None else None).isin(_dep_scope_set)]
        if grp_scope_ids not in (None, []):
            _grp_scope_set = {str(x) for x in grp_scope_ids}
            kpi_df = kpi_df[kpi_df["grupo_id"].isin(_grp_scope_set)]

    base = apply_filters(base, dep_scope_ids, grp_scope_ids)
    if base.empty and dep_scope_ids not in (None, []):
        # fallback defensivo: quando o vínculo vier por departamento e a lista de grupos
        # estiver desatualizada, mantém o filtro por departamento em vez de zerar o dashboard.
        base = apply_filters(base=raw_base, departamento_ids=dep_scope_ids, grupo_ids=None)

    dashboard_groups = group_progress(base)
    base_overall = overall_from_base(base)
    kpi_overall = _overall_from_group_kpis(kpi_df) if not kpi_df.empty else {"pct": 0}
    use_kpi_fallback = bool((base.empty or float(base_overall.get("pct", 0) or 0) <= 0) and float(kpi_overall.get("pct", 0) or 0) > 0)
    if use_kpi_fallback:
        dashboard_groups = _groups_from_kpi_df(kpi_df, gid_to_name, gid_to_dept)

    st.markdown("### Filtros")
    c1, c2, c3 = st.columns([1.1, 1.4, 0.6])
    with c1:
        dept_selected_ids = multiselect_departamentos(
            departamentos,
            key="dash_filter_dept",
            allowed_ids=dep_scope_ids,
        )

    with c2:
        group_selected_ids = multiselect_grupos(
            grupos,
            key="dash_filter_group",
            allowed_group_ids=grp_scope_ids,
            departamento_ids=dept_selected_ids,
        )
    top_n = int(
        c3.selectbox(
            "Top",
            options=[
                5,
                10,
                15],
            index=1,
            key="dash_filter_top"))

    # Sem seleção manual, o dashboard deve usar automaticamente todo o escopo disponível.
    all_visible_dept_ids = [str(d.get("id")) for d in departamentos if d.get("id")]
    all_visible_group_ids = [str(g.get("id")) for g in grupos if g.get("id")]

    # IMPORTANTE: quando nenhum filtro manual foi selecionado, passa None para
    # apply_filters em vez de passar a lista completa de IDs. Isso evita que
    # equipamentos com departamento_id NULL sejam descartados silenciosamente
    # (apply_filters com lista não-vazia filtra por igualdade, e NULL nunca
    # está na lista, zerando o dashboard mesmo com dados existentes).
    effective_dept_ids = [str(x) for x in dept_selected_ids] if dept_selected_ids else None
    if group_selected_ids:
        effective_group_ids = [str(x) for x in group_selected_ids]
    else:
        if dept_selected_ids:
            dept_set = {str(x) for x in dept_selected_ids}
            grp_ids = [
                str(g.get("id"))
                for g in grupos
                if g.get("id") and str(g.get("departamento_id")) in dept_set
            ]
            effective_group_ids = grp_ids if grp_ids else None
        else:
            effective_group_ids = None

    selection_summary(
        "Filtro aplicado",
        {
            "Departamentos": len(dept_selected_ids) if dept_selected_ids else "Todos",
            "Grupos": len(group_selected_ids) if group_selected_ids else "Todos",
            "Ranking": f"Top {top_n}",
        },
    )

    base_filtered = apply_filters(base, effective_dept_ids, effective_group_ids)
    dashboard_groups_filtered = dashboard_groups.copy()
    if use_kpi_fallback and not dashboard_groups_filtered.empty:
        if effective_dept_ids and "departamento_id" in dashboard_groups_filtered.columns:
            _eff_dept = {str(x) for x in effective_dept_ids}
            dashboard_groups_filtered = dashboard_groups_filtered[
                dashboard_groups_filtered["departamento_id"].map(lambda v: str(v) if pd.notna(v) else None).isin(_eff_dept)
            ]
        if effective_group_ids and "grupo_id" in dashboard_groups_filtered.columns:
            _eff_grp = {str(x) for x in effective_group_ids}
            dashboard_groups_filtered = dashboard_groups_filtered[
                dashboard_groups_filtered["grupo_id"].map(lambda v: str(v) if pd.notna(v) else None).isin(_eff_grp)
            ]
    if effective_dept_ids and "departamento_id" in dashboard_groups_filtered.columns:
        effective_dept_set = {str(x) for x in effective_dept_ids}
        dashboard_groups_filtered = dashboard_groups_filtered[
            dashboard_groups_filtered["departamento_id"].map(lambda v: str(v) if pd.notna(v) else None).isin(effective_dept_set)
        ]
    if effective_group_ids and "grupo_id" in dashboard_groups_filtered.columns:
        effective_group_set = {str(x) for x in effective_group_ids}
        dashboard_groups_filtered = dashboard_groups_filtered[
            dashboard_groups_filtered["grupo_id"].map(lambda v: str(v) if pd.notna(v) else None).isin(effective_group_set)
        ]

    if base_filtered.empty and dashboard_groups_filtered.empty:
        notice_card(
            "Nenhum resultado para os filtros",
            "A combinação de departamentos e grupos não retornou dados nesta revisão. Ajuste os filtros para continuar.",
            tone="warning",
        )
        return

    # Quando a base detalhada vier vazia/zerada para perfis com escopo, usa a
    # fonte consolidada por grupo para não exibir todos os percentuais como 0.
    if use_kpi_fallback:
        overall = _overall_from_group_kpis(kpi_df if effective_group_ids is None and effective_dept_ids is None else dashboard_groups_filtered.rename(columns={"pct_concluido": "pct"}))
    else:
        overall = overall_from_base(base_filtered)

    _fragment_kpis_globais(overall)
    st.divider()

    if not use_kpi_fallback:
        with st.spinner("", show_time=False):
            risco, previsao, heat, crit, trend = build_inteligencia(base_filtered)
    else:
        risco, previsao = {"status_risco": "baixo", "risco_score": 0}, {"status_previsao": "sem_base"}
        heat = crit = trend = pd.DataFrame()
    _fragment_previsao(previsao, risco)
    st.divider()

    tabs = [
        "Departamentos",
        "Grupos",
        "Equipamentos",
        "Heatmap",
        "Criticidade",
        "Tendência semanal"]

    def _on_tab_change() -> None:
        st.session_state["_dash_tab"] = st.session_state["_dash_tab_ctrl"]

    active = st.session_state.get("_dash_tab", tabs[0])
    if active not in tabs:
        active = tabs[0]

    st.segmented_control(
        "Visão",
        tabs,
        default=active,
        key="_dash_tab_ctrl",
        on_change=_on_tab_change,
        label_visibility="collapsed")
    active = st.session_state.get("_dash_tab", tabs[0])

    if active == "Grupos":
        st.markdown("### Progresso por grupo")
        _fragment_grupos(
            base_filtered,
            dept_map,
            dashboard_groups_filtered,
            top_n=top_n)
    elif active == "Departamentos":
        st.markdown("### Progresso por departamento")
        _fragment_departamentos(dashboard_groups_filtered, gid_to_dept, dept_map)
    elif active == "Equipamentos":
        st.markdown("### Progresso por equipamento")
        if use_kpi_fallback:
            empty_message("Detalhamento por equipamento indisponível neste perfil; exibindo KPIs consolidados por grupo.")
        else:
            _fragment_equipamentos(base_filtered, dept_map, top_n=top_n)
    elif active == "Heatmap":
        st.markdown("### Heatmap de risco — Grupo × Setor")
        if use_kpi_fallback:
            empty_message("Heatmap indisponível no fallback consolidado desta revisão.")
        else:
            _fragment_heatmap(heat)
    elif active == "Criticidade":
        st.markdown("### Top equipamentos críticos")
        if use_kpi_fallback:
            empty_message("Criticidade detalhada indisponível no fallback consolidado desta revisão.")
        else:
            _fragment_criticidade(crit)
    else:
        st.markdown("### Tendência semanal")
        if use_kpi_fallback:
            empty_message("Tendência semanal indisponível no fallback consolidado desta revisão.")
        else:
            _fragment_tendencia(trend)
