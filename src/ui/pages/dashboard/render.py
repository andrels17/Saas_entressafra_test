"""Dashboard — camada de renderização.

Responsabilidade: exibir KPIs, progresso por grupo/setor/equipamento,
heatmap de risco e tendência semanal, tudo a partir dos dados
calculados em transforms.py.
"""
from __future__ import annotations

import time

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.auth.scope import get_my_scope
from src.auth.permissions import can_view_all_data
from src.domain.kpi import calc_global_kpis, calc_dept_kpis
from src.ui.core.empty_state import empty_state
from src.ui.core.plotly_theme import apply_dark_theme, DARK_LAYOUT, MUTED
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
from src.utils.observability import log_error
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

from .data_access import (
    _load_revisao,
    _load_task_rows_with_fallback,
    _sb_from_token,
    _token_cache_key,
)


@st.cache_data(ttl=30, show_spinner=False)
def _load_base_cached(tenant_id: str, revisao_id: str, token_key: str = "",
                      ver: str = "0", _token: str = "") -> tuple[list, list, list, dict]:
    _ = token_key, ver
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

    debug_meta = {
        "task_rows_current_revision": 0,
        "task_rows_any_revision_visible": 0,
        "latest_visible_revisao_ids": [],
        "diagnostic_hint": "",
        "task_source": "table",
        "task_rpc_used": None,
        "task_rpc_available": False,
        "task_load_error": "",
    }

    try:
        task_rows, task_meta = _load_task_rows_with_fallback(sb, tenant_id, revisao_id, _fetch_all)
        debug_meta.update(task_meta)
    except Exception as exc:
        log_error(exc, context="dashboard._load_base_cached", table="tarefas_servico")
        task_rows = []
    debug_meta["task_rows_current_revision"] = len(task_rows)

    if not task_rows:
        try:
            visible_tasks_sample = (
                sb.table("tarefas_servico")
                .select("revisao_id,updated_at")
                .eq("tenant_id", tenant_id)
                .order("updated_at", desc=True)
                .range(0, 199)
                .execute()
                .data
            ) or []
            visible_rev_ids = []
            for row in visible_tasks_sample:
                rid = str(row.get("revisao_id") or "").strip()
                if rid and rid not in visible_rev_ids:
                    visible_rev_ids.append(rid)
            debug_meta["task_rows_any_revision_visible"] = len(visible_tasks_sample)
            debug_meta["latest_visible_revisao_ids"] = visible_rev_ids[:5]
            if visible_tasks_sample and str(revisao_id) not in visible_rev_ids:
                debug_meta["diagnostic_hint"] = "revisao_sem_tarefas_visiveis"
            elif visible_tasks_sample:
                debug_meta["diagnostic_hint"] = "escopo_ou_rls_filtrando_tarefas"
            else:
                debug_meta["diagnostic_hint"] = "rls_sem_visibilidade_em_tarefas"
                if not debug_meta.get("task_rpc_available"):
                    debug_meta["diagnostic_hint"] = "rpc_ausente_e_rls_bloqueando_tarefas"
        except Exception as exc:
            log_error(exc, context="dashboard._load_base_cached.diagnostic", table="tarefas_servico")
            debug_meta["diagnostic_hint"] = "diagnostico_indisponivel"

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
                .select("id,frota,modelo,grupo_id,ativo")
                .eq("tenant_id", tenant_id)
                .eq("ativo", True)
            )
        except Exception as exc:
            log_error(exc, context="dashboard._load_base_cached", table="equipamentos")

    if not eq_rows:
        try:
            eq_rows = _fetch_all(
                sb.table("equipamentos")
                .select("id,frota,modelo,grupo_id,ativo")
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
                    .select("id,frota,modelo,grupo_id,ativo")
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
            .select("id,nome,departamento_id,ativo")
            .eq("tenant_id", tenant_id)
        )
    except Exception as exc:
        log_error(exc, context="dashboard._load_base_cached", table="equip_grupos")
        grupo_rows = []

    # Se a RPC de equipamentos não devolver a coluna ativo, complementa com a tabela.
    # Isso impede que equipamentos desativados apareçam no ranking "Menor para maior".
    try:
        eq_status_rows = _fetch_all(
            sb.table("equipamentos")
            .select("id,ativo")
            .eq("tenant_id", tenant_id)
        )
        eq_status_map = {str(r.get("id")): r.get("ativo") for r in eq_status_rows if r.get("id") is not None}
        for _eq in eq_rows:
            _eid = str(_eq.get("id")) if _eq.get("id") is not None else ""
            if _eid in eq_status_map:
                _eq["ativo"] = eq_status_map[_eid]
    except Exception as exc:
        log_error(exc, context="dashboard._load_base_cached.eq_status", table="equipamentos")

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
            "row_source": "task",
            "equipamento_id": t.get("equipamento_id"),
            "grupo_id": gid,
            "grupo_nome": grp.get("nome"),
            "departamento_id": eq.get("departamento_id") or grp.get("departamento_id"),
            "frota": eq.get("frota"),
            "modelo": eq.get("modelo"),
            "equipamento_ativo": eq.get("ativo", True),
            "grupo_ativo": grp.get("ativo", True),
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
                "row_source": "synthetic",
                "equipamento_id": eq.get("id"),
                "grupo_id": gid,
                "grupo_nome": grp.get("nome"),
                "departamento_id": eq.get("departamento_id") or grp.get("departamento_id"),
                "frota": eq.get("frota"),
                "modelo": eq.get("modelo"),
                "equipamento_ativo": eq.get("ativo", True),
                "grupo_ativo": grp.get("ativo", True),
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

    # Base dedicada da aba Equipamentos: além da grade sintética, inclui
    # também as tarefas reais dos equipamentos já cobertos por grupo_servicos.
    # Isso permite que o ranking híbrido sobrescreva corretamente o progresso
    # por equipamento quando a junção sintética não conseguir casar alguma
    # combinação equipamento × serviço, sem inflar os KPIs globais das outras abas.
    covered_task_rows = [t for t in raw_tasks if str(t.get("equipamento_id") or "") in eids_covered]
    raw_equipment = list(raw)
    if covered_task_rows:
        raw_equipment.extend(covered_task_rows)

    # Fallback total: se nenhuma grade pôde ser montada, usa todas as tarefas.
    if not raw and raw_tasks:
        raw = raw_tasks
    if not raw_equipment and raw_tasks:
        raw_equipment = list(raw_tasks)

    eq_meta = [
        {
            "equipamento_id": r.get("id"),
            "frota": r.get("frota"),
            "modelo": r.get("modelo"),
            "grupo_id": r.get("grupo_id"),
            "ativo": r.get("ativo", True),
            "equipamento_ativo": r.get("ativo", True),
            "departamento_id": r.get("departamento_id"),
        }
        for r in eq_rows
    ]
    return raw, raw_equipment, eq_meta, debug_meta


@st.cache_data(ttl=120, show_spinner=False)
def _load_departamentos(tenant_id: str, token_key: str = "", ver: str = "0", _token: str = "") -> list[dict]:
    _ = token_key, ver
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
def _load_grupos(tenant_id: str, token_key: str = "", ver: str = "0", _token: str = "") -> list[dict]:
    _ = token_key, ver
    sb = _sb_from_token(_token)
    try:
        return (
            sb.table("equip_grupos")
            .select("id,nome,departamento_id,ativo")
            .eq("tenant_id", tenant_id)
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


def _pct_status(v: float) -> str:
    if v >= 80:
        return "Avançado"
    if v >= 50:
        return "Atenção"
    return "Crítico"


def _is_active_value(value) -> bool:
    """Interpreta a coluna ativo vinda do Supabase sem descartar dados quando ela não existe."""
    if value is None or pd.isna(value):
        return True
    if isinstance(value, bool):
        return value
    txt = str(value).strip().lower()
    return txt not in {"false", "0", "não", "nao", "n", "inativo", "inactive"}


def _filter_active_base(df: pd.DataFrame, active_group_ids: set[str] | None = None,
                        active_equipment_ids: set[str] | None = None) -> pd.DataFrame:
    """Remove grupos/equipamentos inativos sem quebrar perfis que não recebem a coluna ativo."""
    if df is None or df.empty:
        return df
    out = df.copy()
    if "grupo_ativo" in out.columns:
        out = out[out["grupo_ativo"].map(_is_active_value)]
    if active_group_ids is not None and "grupo_id" in out.columns:
        out = out[out["grupo_id"].map(lambda v: str(v) if pd.notna(v) and v is not None else None).isin(active_group_ids)]
    if "equipamento_ativo" in out.columns:
        out = out[out["equipamento_ativo"].map(_is_active_value)]
    if active_equipment_ids is not None and "equipamento_id" in out.columns:
        out = out[out["equipamento_id"].map(lambda v: str(v) if pd.notna(v) and v is not None else None).isin(active_equipment_ids)]
    return out


def _filter_groups_by_ids(df: pd.DataFrame, allowed_group_ids: set[str] | None) -> pd.DataFrame:
    if df is None or df.empty or allowed_group_ids is None or "grupo_id" not in df.columns:
        return df
    return df[df["grupo_id"].map(lambda v: str(v) if pd.notna(v) and v is not None else None).isin(allowed_group_ids)].copy()


def _risk_scale() -> list[list[float | str]]:
    return [
        [0.0, "#12B76A"],
        [0.49, "#12B76A"],
        [0.50, "#F59E0B"],
        [0.79, "#F59E0B"],
        [0.80, "#EF4444"],
        [1.0, "#EF4444"],
    ]


def _apply_semantic_bar_style(fig: go.Figure, chart_df: pd.DataFrame, value_col: str, category_col: str) -> go.Figure:
    if chart_df is None or chart_df.empty:
        return fig
    fig.update_traces(
        marker_color=chart_df[value_col].apply(_pct_bar_color).tolist(),
        marker_line_color="rgba(255,255,255,0.10)",
        marker_line_width=1,
        textfont=dict(color="#E8EDF5", size=12),
        hoverlabel=dict(font=dict(color="#E8EDF5")),
        customdata=chart_df[[category_col]].to_numpy(),
    )
    return fig


def _build_gestor_rank_df(
        dashboard_groups_filtered: pd.DataFrame,
        gestor_options: list[dict],
        top_n: int = 10,
        sort_ascending: bool = False) -> pd.DataFrame:
    if dashboard_groups_filtered is None or dashboard_groups_filtered.empty or not gestor_options:
        return pd.DataFrame(columns=["Categoria", "Valor", "label", "Grupos", "status"])

    src = dashboard_groups_filtered.copy()
    if "grupo_id" not in src.columns:
        return pd.DataFrame(columns=["Categoria", "Valor", "label", "Grupos", "status"])
    src["grupo_id"] = src["grupo_id"].map(lambda v: str(v) if pd.notna(v) else None)

    rows: list[dict] = []
    for gestor in gestor_options:
        gestor_nome = str(gestor.get("gestor_nome") or "—").strip() or "—"
        gids = {
            str(g.get("grupo_id") or "").strip()
            for g in (gestor.get("grupos") or [])
            if str(g.get("grupo_id") or "").strip()
        }
        if not gids:
            continue
        gdf = src[src["grupo_id"].isin(gids)].copy()
        if gdf.empty:
            continue
        done_steps = pd.to_numeric(gdf.get("done_steps", 0), errors="coerce").fillna(0).sum()
        expected_steps = pd.to_numeric(gdf.get("expected_steps", 0), errors="coerce").fillna(0).sum()
        pct = float(round((done_steps / expected_steps) * 100, 0)) if expected_steps > 0 else 0.0
        rows.append({
            "Categoria": gestor_nome,
            "Valor": max(0.0, min(100.0, pct)),
            "label": f"{int(round(pct))}%",
            "Grupos": int(gdf["grupo_id"].nunique()),
            "status": _pct_status(pct),
        })

    if not rows:
        return pd.DataFrame(columns=["Categoria", "Valor", "label", "Grupos", "status"])

    out = pd.DataFrame(rows)
    out = out.sort_values(["Valor", "Grupos", "Categoria"], ascending=[sort_ascending, False, True]).head(top_n)
    return out


@st.cache_data(ttl=30, show_spinner=False)
def _load_weekly_trend_view(
        tenant_id: str,
        revisao_id: str,
        semanas_total: int | None = None,
        token_key: str = "",
        ver: str = "0",
        _token: str = "") -> pd.DataFrame:
    _ = token_key, ver
    sb = _sb_from_token(_token)
    cols = ["week_number", "semana_label", "pct_real", "pct_ideal", "delta_pct"]
    try:
        rows = (
            sb.table("v_evolucao_semanal_revisao")
            .select("semana_operacional,pct_cum")
            .eq("tenant_id", tenant_id)
            .eq("revisao_id", revisao_id)
            .order("semana_operacional")
            .execute()
            .data or []
        )
    except Exception as exc:
        log_error(exc, context="dashboard._load_weekly_trend_view", table="v_evolucao_semanal_revisao")
        return pd.DataFrame(columns=cols)

    if not rows:
        return pd.DataFrame(columns=cols)

    df = pd.DataFrame(rows)
    if df.empty or "semana_operacional" not in df.columns:
        return pd.DataFrame(columns=cols)

    df["semana_operacional"] = pd.to_numeric(df["semana_operacional"], errors="coerce")
    df["pct_cum"] = pd.to_numeric(df.get("pct_cum"), errors="coerce")
    df = df.dropna(subset=["semana_operacional"]).copy()
    if df.empty:
        return pd.DataFrame(columns=cols)

    df["semana_operacional"] = df["semana_operacional"].astype(int)
    df = df.sort_values("semana_operacional").drop_duplicates("semana_operacional", keep="last")

    max_week_data = int(df["semana_operacional"].max()) if not df.empty else 0
    planned_weeks = int(semanas_total or 0)
    total_weeks = max(1, planned_weeks, max_week_data)

    pct_map = {int(r["semana_operacional"]): float(r["pct_cum"]) for _, r in df.iterrows()}
    rows_out: list[dict] = []
    for week in range(1, total_weeks + 1):
        pct_real = pct_map.get(week)
        pct_ideal = round((week / max(planned_weeks, 1)) * 100, 2) if planned_weeks > 0 else None
        delta = round(float(pct_real) - float(pct_ideal), 2) if pct_real is not None and pct_ideal is not None else None
        rows_out.append({
            "week_number": week,
            "semana_label": f"S{week}",
            "pct_real": pct_real if pct_real is not None else None,
            "pct_ideal": pct_ideal,
            "delta_pct": delta,
        })
    return pd.DataFrame(rows_out, columns=cols)

@st.cache_data(ttl=120, show_spinner=False)
def _load_gestor_options(tenant_id: str, token_key: str = "", ver: str = "0", _token: str = "") -> list[dict]:
    _ = token_key, ver, _token
    try:
        from src.ui.pages.notificacoes.data import load_manager_print_options
        return load_manager_print_options(tenant_id, ver=str(ver), _token=_token) or []
    except Exception as exc:
        log_error(exc, context="dashboard._load_gestor_options")
        return []


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
    chart_df[value_col] = pd.to_numeric(chart_df[value_col], errors="coerce").fillna(0).clip(0, 100)
    chart_df = chart_df.sort_values(value_col, ascending=False).head(top_n)
    chart_df = chart_df.sort_values(value_col, ascending=True)
    chart_df["label"] = chart_df[value_col].map(lambda v: f"{int(round(v))}%")

    fig = px.bar(
        chart_df,
        x=value_col,
        y=category_col,
        orientation="h",
        text="label",
        title=title,
    )
    _apply_semantic_bar_style(fig, chart_df, value_col, category_col)
    fig.update_traces(
        textposition="outside",
        cliponaxis=False,
        hovertemplate="%{y}<br>% Concluído: %{x:.0f}%<extra></extra>")
    apply_dark_theme(fig, height=max(380, 42 * len(chart_df) + 80))
    fig.update_layout(
        margin=dict(l=10, r=90, t=48, b=10),
        xaxis=dict(range=[0, 110], title="% Concluído", tickfont=dict(color=MUTED), title_font=dict(color=MUTED)),
        yaxis=dict(title="", type="category", tickfont=dict(color=MUTED)),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})



def _build_rank_df_from_groups(
        dashboard_groups_filtered: pd.DataFrame,
        top_n: int = 10,
        sort_ascending: bool = False) -> pd.DataFrame:
    if dashboard_groups_filtered is None or dashboard_groups_filtered.empty:
        return pd.DataFrame(columns=["Categoria", "Valor", "label"])
    src = dashboard_groups_filtered.copy()
    value_col = "pct_concluido" if "pct_concluido" in src.columns else "pct"
    name_col = "grupo" if "grupo" in src.columns else "Grupo"
    if value_col not in src.columns or name_col not in src.columns:
        return pd.DataFrame(columns=["Categoria", "Valor", "label"])
    out = (
        src[[name_col, value_col]]
        .rename(columns={name_col: "Categoria", value_col: "Valor"})
        .copy()
    )
    out["Valor"] = pd.to_numeric(out["Valor"], errors="coerce").fillna(0).clip(0, 100)
    out["label"] = out["Valor"].map(lambda v: f"{int(round(v))}%")
    return out.sort_values(["Valor", "Categoria"], ascending=[sort_ascending, True]).head(top_n)


def _build_rank_df_from_departments(
        dashboard_groups_filtered: pd.DataFrame,
        gid_to_dept: dict,
        dept_map: dict,
        top_n: int = 10,
        sort_ascending: bool = False) -> pd.DataFrame:
    if dashboard_groups_filtered is None or dashboard_groups_filtered.empty:
        return pd.DataFrame(columns=["Categoria", "Valor", "label"])
    tmp = dashboard_groups_filtered.copy()
    if "departamento_id" not in tmp.columns:
        tmp["departamento_id"] = tmp["grupo_id"].map(
            lambda v: gid_to_dept.get(str(v)) if pd.notna(v) else None
        )
    tmp["departamento_id"] = tmp["departamento_id"].map(
        lambda v: str(v) if pd.notna(v) and v is not None else None
    )
    tmp = tmp.dropna(subset=["departamento_id"])
    if tmp.empty:
        return pd.DataFrame(columns=["Categoria", "Valor", "label"])
    agg = (
        tmp.groupby("departamento_id", dropna=True)
        .agg(done_steps=("done_steps", "sum"), expected_steps=("expected_steps", "sum"))
        .reset_index()
    )
    agg = agg[agg["expected_steps"] > 0]
    if agg.empty:
        return pd.DataFrame(columns=["Categoria", "Valor", "label"])
    agg["Categoria"] = agg["departamento_id"].map(lambda v: dept_map.get(str(v), "—"))
    agg["Valor"] = ((agg["done_steps"] / agg["expected_steps"]) * 100).round(0).clip(0, 100)
    agg["label"] = agg["Valor"].map(lambda v: f"{int(round(v))}%")
    return agg[["Categoria", "Valor", "label"]].sort_values(["Valor", "Categoria"], ascending=[sort_ascending, True]).head(top_n)


def _build_rank_df_from_equipment(
        equipment_source: pd.DataFrame,
        top_n: int = 10,
        sort_ascending: bool = False) -> pd.DataFrame:
    if equipment_source is None or equipment_source.empty:
        return pd.DataFrame(columns=["Categoria", "Valor", "label"])
    edf = equipment_progress(equipment_source.copy())
    if edf is None or edf.empty:
        return pd.DataFrame(columns=["Categoria", "Valor", "label"])
    if "Frota" not in edf.columns or "Modelo" not in edf.columns:
        return pd.DataFrame(columns=["Categoria", "Valor", "label"])
    value_col = "% Concluído" if "% Concluído" in edf.columns else "pct_concluido"
    if value_col not in edf.columns:
        return pd.DataFrame(columns=["Categoria", "Valor", "label"])
    edf = edf.copy()

    def _equip_name(row) -> str:
        frota = str(row.get("Frota") or "").strip()
        modelo = str(row.get("Modelo") or "").strip()
        if frota and modelo:
            return f"{frota} — {modelo}"
        return frota or modelo or "—"

    edf["Categoria"] = edf.apply(_equip_name, axis=1)
    edf["Valor"] = pd.to_numeric(edf[value_col], errors="coerce").fillna(0).clip(0, 100)
    edf["label"] = edf["Valor"].map(lambda v: f"{int(round(v))}%")
    return edf[["Categoria", "Valor", "label"]].sort_values(["Valor", "Categoria"], ascending=[sort_ascending, True]).head(top_n)


@st.cache_data(ttl=45, show_spinner=False)
def _build_unified_rank_figure_cached(
        payload_key: str,
        dept_records: list[dict],
        group_records: list[dict],
        equip_records: list[dict],
        gestor_records: list[dict],
        sort_ascending: bool = False):
    _ = payload_key
    datasets = [
        ("Departamentos", dept_records),
        ("Grupos", group_records),
        ("Equipamentos", equip_records),
        ("Gestores", gestor_records),
    ]
    fig = go.Figure()
    for idx, (name, records) in enumerate(datasets):
        df = pd.DataFrame(records or [])
        if df.empty:
            x, y, txt, colors = [], [], [], []
        else:
            df = df.sort_values(["Valor", "Categoria"], ascending=[sort_ascending, True])
            x = df["Valor"].tolist()
            y = df["Categoria"].tolist()
            txt = df["label"].tolist()
            colors = df["Valor"].apply(_pct_bar_color).tolist()
        fig.add_trace(
            go.Bar(
                x=x,
                y=y,
                orientation="h",
                text=txt,
                textposition="outside",
                marker=dict(color=colors, line=dict(color="rgba(255,255,255,0.10)", width=1)),
                name=name,
                visible=(idx == 0),
                hovertemplate="%{y}<br>% Concluído: %{x:.0f}%<extra></extra>",
            )
        )

    buttons = []
    for idx, (name, _) in enumerate(datasets):
        visible = [False] * len(datasets)
        visible[idx] = True
        buttons.append(
            dict(
                label=name,
                method="update",
                args=[{"visible": visible}, {"title": f"Visão consolidada — {name}"}],
            )
        )

    max_items = max([len(dept_records or []), len(group_records or []), len(equip_records or []), len(gestor_records or []), 1])

    apply_dark_theme(fig, height=max(420, 42 * max_items + 120))
    fig.update_layout(
        title="Visão consolidada — Departamentos",
        margin=dict(l=10, r=90, t=110, b=10),
        xaxis=dict(range=[0, 110], title="% Concluído", tickfont=dict(color=MUTED), title_font=dict(color=MUTED)),
        yaxis=dict(title="", type="category", tickfont=dict(color=MUTED), autorange="reversed"),
        showlegend=False,
    )
    fig.update_layout(
        updatemenus=[
            dict(
                type="dropdown",
                direction="down",
                x=1.0,
                xanchor="right",
                y=1.16,
                yanchor="top",
                showactive=True,
                bgcolor="#0D1B2A",
                bordercolor="rgba(255,255,255,0.12)",
                borderwidth=1,
                font=dict(color="#E8EDF5", size=12, family="DM Sans, sans-serif"),
                pad=dict(t=6, r=6, b=6, l=6),
                buttons=buttons,
            )
        ],
    )
    return fig


def _render_unified_rank_chart(
        dashboard_groups_filtered: pd.DataFrame,
        gid_to_dept: dict,
        dept_map: dict,
        equipment_source: pd.DataFrame,
        gestor_options: list[dict] | None = None,
        top_n: int = 10) -> None:
    ordem_label = st.radio(
        "Ordenar ranking",
        options=["Maior para menor", "Menor para maior"],
        index=0,
        horizontal=True,
        key="dash_rank_order",
        help="Alterne a ordenação do ranking consolidado entre maior e menor percentual concluído.",
    )
    sort_ascending = ordem_label == "Menor para maior"

    dept_df = _build_rank_df_from_departments(
        dashboard_groups_filtered, gid_to_dept, dept_map, top_n=top_n, sort_ascending=sort_ascending
    )
    group_df = _build_rank_df_from_groups(
        dashboard_groups_filtered, top_n=top_n, sort_ascending=sort_ascending
    )
    equip_df = _build_rank_df_from_equipment(
        equipment_source, top_n=top_n, sort_ascending=sort_ascending
    )
    gestor_df = _build_gestor_rank_df(
        dashboard_groups_filtered, gestor_options or [], top_n=top_n, sort_ascending=sort_ascending
    )

    if dept_df.empty and group_df.empty and equip_df.empty and gestor_df.empty:
        st.info("Sem dados para exibir.")
        return

    payload_key = str(hash(
        str(dept_df.to_dict("records")[:50])
        + str(group_df.to_dict("records")[:50])
        + str(equip_df.to_dict("records")[:50])
        + str(gestor_df.to_dict("records")[:50])
        + str(top_n)
        + str(sort_ascending)
    ))
    fig = _build_unified_rank_figure_cached(
        payload_key,
        dept_df.to_dict("records"),
        group_df.to_dict("records"),
        equip_df.to_dict("records"),
        gestor_df.to_dict("records"),
        sort_ascending=sort_ascending,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.caption("Use o seletor no canto superior direito para alternar entre departamentos, grupos, equipamentos e gestores.")



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

    fig = px.bar(
        chart_df,
        x="% Concluído",
        y="Departamento",
        orientation="h",
        text="label",
        title="Progresso por departamento — clique numa barra para filtrar",
    )
    _apply_semantic_bar_style(fig, chart_df, "% Concluído", "Departamento")
    fig.update_traces(
        textposition="outside",
        cliponaxis=False,
        hovertemplate="%{y}<br>% Concluído: %{x:.0f}%<br><i>Clique para filtrar</i><extra></extra>",
    )
    apply_dark_theme(fig, height=max(320, 52 * len(chart_df) + 80))
    fig.update_layout(
        margin=dict(l=10, r=90, t=52, b=10),
        xaxis=dict(range=[0, 110], title="% Concluído",
                   tickfont=dict(color="#8A9BAE"), title_font=dict(color="#8A9BAE")),
        yaxis=dict(title="", type="category",
                   tickfont=dict(color="#8A9BAE")),
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
    base_eq = base.copy()

    # Para perfis de gestor, a grade sintética via grupo_servicos pode empurrar
    # todos os equipamentos para 0%. Por outro lado, usar SOMENTE as linhas de
    # tarefas pode esconder equipamentos cuja cobertura ainda depende da grade.
    # Estratégia híbrida: calcula no base completo e, quando houver linhas reais
    # de tarefa, sobrescreve apenas os equipamentos presentes nelas.
    edf_full = equipment_progress(base_eq)
    edf = edf_full.copy()
    if not base_eq.empty and "row_source" in base_eq.columns:
        task_only = base_eq[base_eq["row_source"].astype(str).eq("task")].copy()
        if not task_only.empty:
            edf_task = equipment_progress(task_only)
            if not edf_task.empty and "equipamento_id" in edf_task.columns:
                edf_full = edf_full.copy() if not edf_full.empty else pd.DataFrame(columns=edf_task.columns)
                edf_full["equipamento_id"] = edf_full.get("equipamento_id", pd.Series(dtype=object)).map(
                    lambda v: str(v).strip() if pd.notna(v) and str(v).strip() else None
                )
                edf_task["equipamento_id"] = edf_task["equipamento_id"].map(
                    lambda v: str(v).strip() if pd.notna(v) and str(v).strip() else None
                )
                if edf_full.empty:
                    edf = edf_task.copy()
                else:
                    task_ids = set(edf_task["equipamento_id"].dropna())
                    edf = pd.concat(
                        [
                            edf_task,
                            edf_full[~edf_full["equipamento_id"].isin(task_ids)],
                        ],
                        ignore_index=True,
                    )
            else:
                edf = edf_full.copy()
        else:
            edf = edf_full.copy()

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
        color_continuous_scale=_risk_scale(),
        labels={
            "setor": "Setor",
            "grupo": "Grupo",
            "calor_score": "Score de risco"},
        title="Heatmap de Risco — Grupo × Setor",
    )
    apply_dark_theme(fig, height=max(300, len(heat["grupo"].unique()) * 40 + 80))
    fig.update_layout(margin=dict(l=10, r=10, t=40, b=10))
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
        color_discrete_map={"Real": "#12B76A", "Ideal": MUTED},
        line_dash_map={"Real": "solid", "Ideal": "dash"},
    )
    fig.update_traces(hovertemplate="%{x}<br>%{fullData.name}: %{y:.0f}%<extra></extra>")
    apply_dark_theme(fig, height=360, xaxis_title="Semana", yaxis_title="% Concluído")
    fig.update_layout(
        margin=dict(l=10, r=10, t=48, b=10),
        yaxis=dict(range=[0, 100], tickfont=dict(color="#8A9BAE"),
                   title_font=dict(color="#8A9BAE")),
        legend_title_text="",
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})





def _groups_from_kpi_df(kdf: pd.DataFrame, gid_to_name: dict, gid_to_dept: dict) -> pd.DataFrame:
    if kdf is None or kdf.empty:
        return pd.DataFrame(columns=[
            "grupo", "grupo_id", "departamento_id", "pct_concluido", "done_steps", "expected_steps"
        ])
    tmp = kdf.copy()
    tmp["grupo_id"] = tmp["grupo_id"].map(lambda v: str(v) if pd.notna(v) else None)
    tmp["departamento_id"] = tmp["grupo_id"].map(lambda v: gid_to_dept.get(str(v)) if v is not None else None)
    tmp["grupo"] = tmp["grupo_id"].map(
        lambda v: gid_to_name.get(str(v)) or f"Grupo sem nome ({str(v)[:8]})" if v is not None else "—"
    )
    tmp["done_steps"] = pd.to_numeric(tmp.get("done_steps", 0), errors="coerce").fillna(0).astype(int)
    tmp["expected_steps"] = pd.to_numeric(tmp.get("expected_steps", 0), errors="coerce").fillna(0).astype(int)
    tmp["pct_concluido"] = (
        (tmp["done_steps"] / tmp["expected_steps"].replace(0, pd.NA) * 100)
        .round(0)
        .fillna(0)
        .clip(0, 100)
    )
    return tmp[["grupo", "grupo_id", "departamento_id", "pct_concluido", "done_steps", "expected_steps"]].copy()


def _overall_from_group_kpis(kdf: pd.DataFrame) -> dict:
    """Calcula overall a partir de um DataFrame de grupos.

    Aceita dois formatos:
    - kpi_df bruto (colunas eq_count, svc_count, done_steps, expected_steps)
      → delega para calc_global_kpis.
    - DataFrame resumido de group_progress / _groups_from_kpi_df
      (colunas done_steps, expected_steps, sem eq_count/svc_count)
      → calcula pct diretamente para evitar KeyError em calc_global_kpis.
    """
    if kdf is None or kdf.empty:
        return {"pct": 0.0, "total": 0, "concl": 0, "sem_inicio": 0,
                "andamento": 0, "atrasados": 0, "pend": 0, "trav": 0, "na": 0}

    if "eq_count" in kdf.columns and "svc_count" in kdf.columns:
        # Formato kpi_df completo — caminho original
        gk = calc_global_kpis(kdf)
        pct = float(gk.get("pct", 0) or 0)
    else:
        # Formato resumido (done_steps / expected_steps apenas)
        done = pd.to_numeric(kdf.get("done_steps", 0), errors="coerce").fillna(0).sum()
        expected = pd.to_numeric(kdf.get("expected_steps", 0), errors="coerce").fillna(0).sum()
        pct = round(float(done) / max(float(expected), 1) * 100, 1) if expected else 0.0
        pct = max(0.0, min(100.0, pct))

    return {
        "pct": pct,
        "total": int(len(kdf)),
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
    role_norm = str(role or "").strip().lower()
    can_use_gestor_filter = role_norm in {"admin", "supervisor", "superadmin"}
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

    ver = str(st.session_state.get("data_version", "0"))
    token = st.session_state.get("sb_access_token", "")
    with st.spinner("", show_time=False):
        token_key = _token_cache_key(token)
        raw, raw_equipment, eq_meta, debug_meta = _load_base_cached(
            tenant_id, revisao_id, token_key=token_key, ver=ver, _token=token
        )
        departamentos = _load_departamentos(tenant_id, token_key=token_key, ver=ver, _token=token)
        grupos = _load_grupos(tenant_id, token_key=token_key, ver=ver, _token=token)
        gestor_options = (
            _load_gestor_options(tenant_id, token_key=token_key, ver=ver, _token=token)
            if can_use_gestor_filter
            else []
        )

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

    # A Visão consolidada deve considerar somente cadastros ativos por padrão.
    # Isso evita que o ranking "Menor para maior" seja preenchido por grupos/equipamentos antigos 0%.
    active_group_ids = {str(g.get("id")) for g in grupos if g.get("id") and _is_active_value(g.get("ativo", True))}
    active_dept_ids = {str(d.get("id")) for d in departamentos if d.get("id") and _is_active_value(d.get("ativo", True))}

    dept_map = {str(d["id"]): d.get("nome", "—")
                for d in departamentos if d.get("id")}
    gid_to_name = {str(g["id"]): g.get("nome", "—") for g in grupos if g.get("id")}
    gid_to_dept = {str(g["id"]): str(g.get("departamento_id")) if g.get("departamento_id") else None
                   for g in grupos if g.get("id")}

    base = normalize_matriz_base(raw, eq_meta)
    equipment_base = normalize_matriz_base(raw_equipment, eq_meta)

    active_equipment_ids: set[str] | None = None
    if eq_meta:
        active_equipment_ids = {
            str(e.get("equipamento_id") or e.get("id"))
            for e in eq_meta
            if (e.get("equipamento_id") or e.get("id")) and _is_active_value(e.get("ativo", e.get("equipamento_ativo", True)))
        }
        if not active_equipment_ids:
            active_equipment_ids = None

    base = _filter_active_base(base, active_group_ids=active_group_ids or None, active_equipment_ids=active_equipment_ids)
    equipment_base = _filter_active_base(equipment_base, active_group_ids=active_group_ids or None, active_equipment_ids=active_equipment_ids)

    # Complementa os mapas com os nomes vindos da base detalhada.
    # Isso é essencial no ranking "Menor para maior": normalmente os primeiros
    # itens são grupos/equipamentos 0%, e alguns deles podem não aparecer na
    # lista filtrada de grupos ativos, embora existam na base da revisão.
    for _df in (base, equipment_base):
        if _df is None or _df.empty or "grupo_id" not in _df.columns:
            continue
        for _, _row in _df[[c for c in ["grupo_id", "grupo_nome", "departamento_id"] if c in _df.columns]].drop_duplicates().iterrows():
            _gid = str(_row.get("grupo_id") or "").strip()
            if not _gid:
                continue
            _gname = str(_row.get("grupo_nome") or "").strip() if "grupo_nome" in _row.index else ""
            if _gname and _gname not in {"—", "None", "nan"}:
                gid_to_name[_gid] = _gname
            _dept = str(_row.get("departamento_id") or "").strip() if "departamento_id" in _row.index else ""
            if _dept:
                gid_to_dept[_gid] = _dept
    # Para equipamentos, preservamos a base completa SOMENTE dentro do escopo do
    # usuário. A combinação híbrida entre linhas sintéticas e linhas reais de
    # tarefa acontece dentro de _fragment_equipamentos(). O problema anterior
    # era manter equipment_base sem aplicar o escopo automático do gestor,
    # fazendo a aba listar equipamentos de outros departamentos/grupos.
    if dep_scope_ids is not None or grp_scope_ids is not None:
        equipment_base = apply_filters(equipment_base, dep_scope_ids, grp_scope_ids)
        if equipment_base.empty and dep_scope_ids not in (None, []):
            # Mesmo fallback defensivo usado na base principal: se o vínculo do
            # gestor vier por departamento e a lista de grupos estiver
            # desatualizada, mantém o recorte por departamento em vez de zerar.
            equipment_base = apply_filters(
                base=base.copy(),
                departamento_ids=dep_scope_ids,
                grupo_ids=None,
            )
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
        if active_group_ids:
            kpi_df = kpi_df[kpi_df["grupo_id"].isin(active_group_ids)]

    base = apply_filters(base, dep_scope_ids, grp_scope_ids)
    if base.empty and dep_scope_ids not in (None, []):
        # fallback defensivo: quando o vínculo vier por departamento e a lista de grupos
        # estiver desatualizada, mantém o filtro por departamento em vez de zerar o dashboard.
        base = apply_filters(base=raw_base, departamento_ids=dep_scope_ids, grupo_ids=None)

    # Grupos no dashboard devem seguir a mesma fonte de verdade da Matriz/PDF.
    # Antes usávamos group_progress(base) e só caíamos no KPI engine como fallback.
    # Isso mantinha divergências quando a base do dashboard continha serviços/linhas
    # diferentes da consolidação oficial. Agora, sempre que houver KPI consolidado,
    # usamos ele como fonte principal para percentuais de grupos.
    dashboard_groups = _groups_from_kpi_df(kpi_df, gid_to_name, gid_to_dept) if not kpi_df.empty else group_progress(base)
    dashboard_groups = _filter_groups_by_ids(dashboard_groups, active_group_ids or None)
    base_overall = overall_from_base(base)
    kpi_overall = _overall_from_group_kpis(kpi_df) if not kpi_df.empty else {"pct": 0}
    use_kpi_fallback = bool((base.empty or float(base_overall.get("pct", 0) or 0) <= 0) and float(kpi_overall.get("pct", 0) or 0) > 0)

    st.markdown("### Filtros")

    gestor_selected_group_ids: list[str] | None = None
    gestor_selected_dept_ids: list[str] | None = None
    departamentos_filter = list(departamentos)
    grupos_filter = list(grupos)

    # Admin/supervisor conseguem simular o recorte de um gestor no Dashboard.
    # O recorte é aplicado antes dos filtros de departamento/grupo, para que
    # os selects exibam apenas os departamentos, grupos e equipamentos do gestor.
    if can_use_gestor_filter and gestor_options:
        gestor_labels = ["Todos os gestores"] + [
            str(g.get("gestor_nome") or "Gestor") for g in gestor_options
        ]
        gestor_by_label = {
            str(g.get("gestor_nome") or "Gestor"): g for g in gestor_options
        }
        gestor_sel_label = st.selectbox(
            "Gestor",
            gestor_labels,
            index=0,
            key="dash_filter_gestor",
            help="Filtra o Dashboard para enxergar somente os departamentos, grupos e equipamentos vinculados ao gestor.",
        )
        gestor_sel = gestor_by_label.get(gestor_sel_label)
        if gestor_sel:
            gestor_selected_group_ids = sorted({
                str(gr.get("grupo_id") or "")
                for gr in (gestor_sel.get("grupos") or [])
                if str(gr.get("grupo_id") or "")
            })
            gestor_selected_dept_ids = sorted({
                str(gr.get("departamento_id") or "")
                for gr in (gestor_sel.get("grupos") or [])
                if str(gr.get("departamento_id") or "")
            })
            dept_set = set(gestor_selected_dept_ids)
            grp_set = set(gestor_selected_group_ids)
            departamentos_filter = [d for d in departamentos if str(d.get("id")) in dept_set]
            grupos_filter = [g for g in grupos if str(g.get("id")) in grp_set]
            selection_summary({
                "Gestor": gestor_sel_label,
                "Departamentos": len(departamentos_filter),
                "Grupos": len(grupos_filter),
            })

    only_manager_linked = False
    manager_group_ids: set[str] = set()
    if can_use_gestor_filter and gestor_options:
        manager_group_ids = {
            str(gr.get("grupo_id") or "").strip()
            for gestor in gestor_options
            for gr in (gestor.get("grupos") or [])
            if str(gr.get("grupo_id") or "").strip()
        }
        only_manager_linked = st.checkbox(
            "Mostrar somente grupos/equipamentos vinculados a gestores",
            value=False,
            key="dash_only_manager_linked",
            help="Quando marcado, remove da visão consolidada os grupos que não estão vinculados a nenhum gestor de relatório.",
        )
        if only_manager_linked and manager_group_ids:
            grupos_filter = [g for g in grupos_filter if str(g.get("id")) in manager_group_ids]

    c1, c2, c3 = st.columns([1.1, 1.4, 0.6])
    with c1:
        dept_allowed_ids = gestor_selected_dept_ids if gestor_selected_dept_ids is not None else dep_scope_ids
        dept_selected_ids = multiselect_departamentos(
            departamentos_filter,
            key="dash_filter_dept",
            allowed_ids=dept_allowed_ids,
        )

    with c2:
        group_allowed_ids = gestor_selected_group_ids if gestor_selected_group_ids is not None else grp_scope_ids
        group_selected_ids = multiselect_grupos(
            grupos_filter,
            key="dash_filter_group",
            allowed_group_ids=group_allowed_ids,
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
    all_visible_dept_ids = [str(d.get("id")) for d in departamentos_filter if d.get("id")]
    all_visible_group_ids = [str(g.get("id")) for g in grupos_filter if g.get("id")]

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
                for g in grupos_filter
                if g.get("id") and str(g.get("departamento_id")) in dept_set
            ]
            effective_group_ids = grp_ids if grp_ids else None
        elif gestor_selected_group_ids is not None:
            effective_group_ids = gestor_selected_group_ids
        else:
            effective_group_ids = None

    if not effective_dept_ids and gestor_selected_dept_ids is not None:
        effective_dept_ids = gestor_selected_dept_ids

    if only_manager_linked and manager_group_ids:
        if effective_group_ids:
            effective_group_ids = [gid for gid in effective_group_ids if str(gid) in manager_group_ids]
        else:
            effective_group_ids = sorted(manager_group_ids.intersection(active_group_ids)) if active_group_ids else sorted(manager_group_ids)

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
        overall = _overall_from_group_kpis(kpi_df if effective_group_ids is None and effective_dept_ids is None else dashboard_groups_filtered)
    else:
        overall = overall_from_base(base_filtered)

    _fragment_kpis_globais(overall)
    st.divider()

    detailed_available = not base_filtered.empty
    equipment_base_filtered = apply_filters(equipment_base, effective_dept_ids, effective_group_ids)
    equipment_detailed_available = not equipment_base_filtered.empty

    equipment_detail_reason = ""
    if not equipment_detailed_available:
        hint = str((debug_meta or {}).get("diagnostic_hint") or "")
        visible_count = int((debug_meta or {}).get("task_rows_any_revision_visible") or 0)
        latest_visible = (debug_meta or {}).get("latest_visible_revisao_ids") or []
        if hint == "rls_sem_visibilidade_em_tarefas":
            equipment_detail_reason = (
                "Este perfil não está enxergando linhas de tarefas_servico. "
                "Os equipamentos aparecem, mas as tarefas da revisão não ficaram visíveis para o login atual."
            )
        elif hint == "revisao_sem_tarefas_visiveis":
            latest_txt = ", ".join([str(x)[:8] for x in latest_visible]) if latest_visible else "—"
            equipment_detail_reason = (
                "Há tarefas visíveis em outras revisões, mas não na revisão atual. "
                f"Revisões com tarefas visíveis recentemente: {latest_txt}."
            )
        elif hint == "escopo_ou_rls_filtrando_tarefas":
            equipment_detail_reason = (
                "Existem tarefas visíveis neste tenant, porém o escopo final do gestor "
                "está filtrando o detalhamento de equipamentos nesta revisão."
            )
        elif visible_count <= 0:
            equipment_detail_reason = "Não há tarefas de equipamento visíveis neste perfil para a revisão atual."

    if detailed_available:
        with st.spinner("", show_time=False):
            risco, previsao, heat, crit, trend = build_inteligencia(base_filtered)
    else:
        risco, previsao = {"status_risco": "baixo", "risco_score": 0}, {"status_previsao": "sem_base"}
        heat = crit = trend = pd.DataFrame()
    _fragment_previsao(previsao, risco)
    st.divider()

    task_rows_current = int((debug_meta or {}).get("task_rows_current_revision") or 0)
    equipment_source_for_chart = equipment_base_filtered.copy()
    if equipment_source_for_chart.empty and detailed_available and task_rows_current > 0:
        equipment_source_for_chart = base_filtered.copy()
    equipment_source_for_chart = _filter_active_base(
        equipment_source_for_chart,
        active_group_ids=active_group_ids or None,
        active_equipment_ids=active_equipment_ids,
    )

    st.markdown("### Visão consolidada")
    _render_unified_rank_chart(
        dashboard_groups_filtered,
        gid_to_dept,
        dept_map,
        equipment_source_for_chart,
        gestor_options=gestor_options,
        top_n=top_n,
    )

    st.divider()
    st.markdown("### Evolução semanal")
    if detailed_available:
        _fragment_tendencia(trend)
    else:
        empty_message("Evolução semanal indisponível no fallback consolidado desta revisão.")
