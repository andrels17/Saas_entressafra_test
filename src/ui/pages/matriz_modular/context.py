from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd
import streamlit as st

from src.auth.permissions import can_edit_matriz, can_view_all_data
from src.auth.scope import get_my_scope
from src.ui.core.cache import clear_cached_functions
from src.ui.core.cache_matrix import invalidate_matriz_cache
from src.utils.supabase_helpers import current_role, current_tenant_id, sb_for_user
from src.utils.timezone import now_brt as _now_brt
from src.utils.weeks import week_from_revisao as _week_from_revisao

from .data import _all_dept_names, _fetch_template, _group_kpis, _load_payload
from .pdf_export import _compute_setor_ok_counts


@dataclass(slots=True)
class MatrixBaseContext:
    tenant_id: str
    sb: Any
    role: str
    dep_scope_ids: list[str] | None
    grp_scope_ids: list[str] | None
    can_view_all: bool
    can_edit: bool
    revisoes: list[dict[str, Any]]
    grupos: list[dict[str, Any]]


@dataclass(slots=True)
class MatrixGroupContext:
    grupo_id: str
    revisao_id: str
    grupo_nome: str
    titulo: str
    rev_row: dict[str, Any] | None
    limit_eq: int
    payload: dict[str, Any]
    eqs: list[dict[str, Any]]
    setor_to_services: dict[str, list[dict[str, Any]]]
    all_services: list[dict[str, Any]]
    tarefas: list[dict[str, Any]]
    task_map: dict[tuple[str, str], dict[str, Any]]
    eq_ocultos_set: set[str]
    eq_ids: list[str]
    eq_label: dict[str, str]
    eq_label_short: dict[str, str]
    semanas_disp: list[int]
    semana_sugerida: int | None
    total_per_eq: int
    resumo_df: pd.DataFrame
    tok_g: int
    eq100_g: int
    pct_geral: int
    setor_rows: list[dict[str, Any]]
    group_atraso_dias: int
    group_rev_start: pd.Timestamp
    svc_ids_all: list[Any]
    view_agg: pd.DataFrame
    sector_tables_for_export: list[tuple[str, pd.DataFrame]]


def ensure_matrix_session_defaults() -> None:
    st.session_state.setdefault("data_version", "0")
    st.session_state.setdefault("matriz_view", "select")
    st.session_state.setdefault("matriz_limit_eq", 120)
    st.session_state.setdefault("matriz_show_legend", False)
    st.session_state.setdefault("matriz_departamento_id", None)
    st.session_state.setdefault("matriz_atraso_dias", 7)


def load_matrix_base_context() -> MatrixBaseContext | None:
    tenant_id = current_tenant_id()
    sb = sb_for_user()
    role = current_role()

    dep_scope_ids, grp_scope_ids = get_my_scope(tenant_id)
    can_view_all = can_view_all_data(role)
    can_edit = can_edit_matriz(str(role).strip().lower())

    if not can_view_all and dep_scope_ids == [] and grp_scope_ids == []:
        st.warning("Você não possui departamentos ou grupos vinculados para visualizar a matriz.")
        return None

    ensure_matrix_session_defaults()

    revisoes = (
        sb.table("revisoes")
        .select("id,titulo,status,created_at,data_inicio,semanas_total")
        .eq("tenant_id", tenant_id)
        .order("created_at", desc=True)
        .execute()
        .data
    ) or []

    gq = (
        sb.table("equip_grupos")
        .select("id,nome,departamento_id")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .order("nome")
    )
    if not can_view_all and dep_scope_ids is not None:
        gq = gq.eq("departamento_id", dep_scope_ids[0]) if len(dep_scope_ids) == 1 else gq.in_("departamento_id", dep_scope_ids)
    grupos = gq.execute().data or []
    if not can_view_all and grp_scope_ids is not None:
        grupos = [g for g in grupos if g["id"] in grp_scope_ids]
    if not grupos:
        st.info("Nenhum grupo disponivel para o seu escopo.")
        return None

    if "matriz_revisao_id" not in st.session_state:
        ativa = next((r for r in revisoes if r.get("status") == "ativa"), None)
        st.session_state["matriz_revisao_id"] = ativa["id"] if ativa else (revisoes[0]["id"] if revisoes else None)
    if "matriz_grupo_id" not in st.session_state:
        st.session_state["matriz_grupo_id"] = grupos[0]["id"]

    return MatrixBaseContext(
        tenant_id=tenant_id,
        sb=sb,
        role=role,
        dep_scope_ids=dep_scope_ids,
        grp_scope_ids=grp_scope_ids,
        can_view_all=can_view_all,
        can_edit=can_edit,
        revisoes=revisoes,
        grupos=grupos,
    )


def handle_toolbar_reload() -> None:
    invalidate_matriz_cache()
    clear_cached_functions(
        _load_payload,
        _group_kpis,
        _all_dept_names,
        _build_group_context_cached,
    )
    st.session_state.pop("_mtz_payload_cache", None)
    st.session_state.pop("_mtz_last_rendered_grupo_id", None)


def validate_group_access(base_ctx: MatrixBaseContext, grupo_id: str) -> bool:
    if not base_ctx.can_view_all and base_ctx.grp_scope_ids is not None and grupo_id not in base_ctx.grp_scope_ids:
        st.warning("Voce nao tem acesso a este grupo.")
        if st.button("Voltar", key="mtz_back_noaccess"):
            st.session_state["matriz_view"] = "select"
            st.rerun()
        return False
    return True


def _invalidate_payload_on_group_change(grupo_id: str) -> None:
    last_rendered = st.session_state.get("_mtz_last_rendered_grupo_id")
    if last_rendered == grupo_id:
        return
    try:
        _load_payload.clear()
    except Exception:
        clear_cached_functions(_load_payload)
    st.session_state["_mtz_last_rendered_grupo_id"] = grupo_id
    st.session_state.pop("_mtz_payload_cache", None)


def _load_payload_with_session_cache(tenant_id: str, grupo_id: str, revisao_id: str, limit_eq: int) -> dict[str, Any]:
    payload_cache = st.session_state.get("_mtz_payload_cache") or {}
    payload_key = (str(tenant_id), str(grupo_id), str(revisao_id), str(limit_eq), str(st.session_state.get("data_version", "0")))
    if payload_cache.get("key") != str(payload_key):
        payload_cache = {
            "key": str(payload_key),
            "data": _load_payload(
                tenant_id,
                grupo_id,
                revisao_id,
                limit_eq,
                st.session_state.get("data_version", "0"),
                st.session_state.get("sb_access_token", ""),
            ),
        }
        st.session_state["_mtz_payload_cache"] = payload_cache
    return payload_cache["data"]


def _load_eq_ocultos(sb: Any, tenant_id: str, revisao_id: str) -> set[str]:
    try:
        from src.utils.eq_oculto import get_ocultos as _get_ocultos
        return _get_ocultos(sb, tenant_id, revisao_id)
    except Exception:
        return set()


def _build_eq_labels(eqs: list[dict[str, Any]], eq_ocultos_set: set[str]) -> tuple[dict[str, str], dict[str, str]]:
    eq_label = {
        e["id"]: (
            f"⊘ {e.get('frota', '')} — {e.get('modelo') or ''}".strip(" —")
            if e["id"] in eq_ocultos_set
            else f"{e.get('frota', '')} — {e.get('modelo') or ''}".strip(" —")
        )
        for e in eqs
    }
    eq_label_short = {
        e["id"]: (
            f"⊘ {(str(e.get('frota') or '')).strip() or str(e.get('id', ''))}"
            if e["id"] in eq_ocultos_set
            else (str(e.get("frota") or "")).strip() or str(e.get("id", ""))
        )
        for e in eqs
    }
    return eq_label, eq_label_short


def _ensure_template(base_ctx: MatrixBaseContext, grupo_id: str, payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    setor_to_services = payload.get("s2s") or {}
    all_services = payload.get("all_s") or []
    if all_services:
        return setor_to_services, all_services

    try:
        s2s2, all2 = _fetch_template(base_ctx.sb, base_ctx.tenant_id, grupo_id)
        if all2:
            invalidate_matriz_cache()
            clear_cached_functions(_load_payload, _group_kpis, _all_dept_names, _build_group_context_cached)
            st.session_state.pop("_mtz_payload_cache", None)
            return s2s2, all2
    except Exception:
        pass

    st.warning("Grupo sem Template configurado (Admin > Templates).")
    if st.button("Voltar", key="mtz_back_notpl"):
        st.session_state["matriz_view"] = "select"
        st.rerun()
    return None


def _resolve_semana_sugerida(rev_row: dict[str, Any] | None) -> int | None:
    rev_data_inicio = None
    rev_semanas_total = None
    try:
        if rev_row and rev_row.get("data_inicio"):
            rev_data_inicio = date.fromisoformat(str(rev_row["data_inicio"])[:10])
        rev_semanas_total = int(rev_row.get("semanas_total") or 0) or None if rev_row else None
    except Exception:
        pass
    return _week_from_revisao(_now_brt().date(), rev_data_inicio, rev_semanas_total)


def _build_resumo_df(
    eqs: list[dict[str, Any]],
    all_services: list[dict[str, Any]],
    task_map: dict[tuple[str, str], dict[str, Any]],
    eq_label: dict[str, str],
) -> tuple[pd.DataFrame, int, int, int]:
    total_per_eq = max(len(all_services), 1) * 3
    resumo_rows: list[dict[str, Any]] = []
    tok_g = 0
    eq100_g = 0

    for e in eqs:
        done = sum(
            int(bool((task_map.get((e["id"], s.get("id"))) or {}).get(f)))
            for s in all_services
            if s.get("id")
            for f in ("etapa_d", "etapa_r", "etapa_m")
        )
        pct = round((done / max(total_per_eq, 1)) * 100)
        resumo_rows.append(
            {
                "Score": pct,
                "%": pct,
                "Equipamento": eq_label.get(e["id"], str(e.get("id"))),
                "Concluidos": int(done),
                "Total": int(total_per_eq),
            }
        )
        tok_g += done
        if done >= (len(all_services) * 3):
            eq100_g += 1

    resumo_df = pd.DataFrame(resumo_rows)
    if not resumo_df.empty:
        resumo_df = resumo_df.sort_values(["Score", "%", "Equipamento"], ascending=[False, True, True]).reset_index(drop=True)

    pct_geral = round((tok_g / max(len(eqs) * len(all_services) * 3, 1)) * 100)
    return resumo_df, tok_g, eq100_g, pct_geral


def _build_view_agg(eqs: list[dict[str, Any]], all_services: list[dict[str, Any]], task_map: dict[tuple[str, str], dict[str, Any]], eq_label: dict[str, str]) -> pd.DataFrame:
    view_agg_rows = []
    for e in eqs:
        for s in all_services:
            sid = s.get("id")
            sname = s.get("nome", "")
            if not sid:
                continue
            t = task_map.get((e["id"], sid)) or {}
            td = t.get("dt_etapa_d")
            tr = t.get("dt_etapa_r")
            tm = t.get("dt_etapa_m")

            def hrs(a: Any, b: Any):
                try:
                    ta = pd.to_datetime(a, utc=True)
                    tb = pd.to_datetime(b, utc=True)
                    return round((tb - ta).total_seconds() / 3600, 1) if pd.notna(ta) and pd.notna(tb) else None
                except BaseException:
                    return None

            view_agg_rows.append(
                {
                    "Frota": eq_label.get(e["id"], str(e.get("id", ""))),
                    "Serviço": sname,
                    "D→R (h)": hrs(td, tr),
                    "R→M (h)": hrs(tr, tm),
                    "D→M (h)": hrs(td, tm),
                }
            )

    return pd.DataFrame(view_agg_rows) if view_agg_rows else pd.DataFrame()


def _build_sector_tables_for_export(
    eqs: list[dict[str, Any]],
    setor_to_services: dict[str, list[dict[str, Any]]],
    task_map: dict[tuple[str, str], dict[str, Any]],
    eq_label_short: dict[str, str],
) -> list[tuple[str, pd.DataFrame]]:
    sector_tables_for_export: list[tuple[str, pd.DataFrame]] = []
    for setor_nome in sorted(setor_to_services.keys()):
        services = sorted(setor_to_services[setor_nome], key=lambda x: (x.get("nome") or "").lower())
        sids = [s["id"] for s in services if s.get("id")]
        snames = [s.get("nome") or str(s.get("id")) for s in services if s.get("id")]
        if not sids:
            continue
        rows = []
        for e in eqs:
            row = {"Equipamento": eq_label_short[e["id"]]}
            for sid, sname in zip(sids, snames):
                t = task_map.get((e["id"], sid)) or {}
                row[f"{sname} D"] = "OK" if t.get("etapa_d") else ""
                row[f"{sname} R"] = "OK" if t.get("etapa_r") else ""
                row[f"{sname} M"] = "OK" if t.get("etapa_m") else ""
            rows.append(row)
        if rows:
            sector_tables_for_export.append((setor_nome, pd.DataFrame(rows)))
    return sector_tables_for_export


@st.cache_data(ttl=60, show_spinner=False)
def _build_group_context_cached(
    tenant_id: str,
    grupo_id: str,
    revisao_id: str,
    limit_eq: int,
    data_version: str,
    sb_access_token: str,
) -> dict[str, Any]:
    sb = sb_for_user()
    payload = _load_payload(
        tenant_id,
        grupo_id,
        revisao_id,
        limit_eq,
        data_version,
        sb_access_token,
    )
    return payload or {}


def build_group_context(base_ctx: MatrixBaseContext) -> MatrixGroupContext | None:
    grupo_id = st.session_state["matriz_grupo_id"]
    revisao_id = st.session_state["matriz_revisao_id"]
    limit_eq = int(st.session_state["matriz_limit_eq"])
    if not revisao_id:
        st.warning("Nenhuma revisao selecionada.")
        return None

    _invalidate_payload_on_group_change(grupo_id)
    if not validate_group_access(base_ctx, grupo_id):
        return None

    rev_row = next((r for r in base_ctx.revisoes if r.get("id") == revisao_id), None)
    titulo = (rev_row.get("titulo") if rev_row else None) or "Revisao"
    grupo_nome = next((g.get("nome") for g in base_ctx.grupos if g.get("id") == grupo_id), str(grupo_id))

    payload = _load_payload_with_session_cache(base_ctx.tenant_id, grupo_id, revisao_id, limit_eq)
    if not payload:
        payload = _build_group_context_cached(
            tenant_id=base_ctx.tenant_id,
            grupo_id=grupo_id,
            revisao_id=revisao_id,
            limit_eq=limit_eq,
            data_version=str(st.session_state.get("data_version", "0")),
            sb_access_token=str(st.session_state.get("sb_access_token", "")),
        )

    eqs = payload.get("eqs") or []
    if not eqs:
        st.info("Nenhum equipamento no grupo.")
        if st.button("Voltar", key="mtz_back_noeq"):
            st.session_state["matriz_view"] = "select"
            st.rerun()
        return None

    eq_ocultos_set = _load_eq_ocultos(base_ctx.sb, base_ctx.tenant_id, revisao_id)
    eq_label, eq_label_short = _build_eq_labels(eqs, eq_ocultos_set)

    template = _ensure_template(base_ctx, grupo_id, payload)
    if not template:
        return None
    setor_to_services, all_services = template

    tarefas = payload.get("tarefas") or []
    task_map = {(str(t["equipamento_id"]), str(t["servico_id"])): t for t in tarefas}
    semanas_disp = sorted({int(t.get("semana") or 0) for t in tarefas if t.get("semana")})
    semana_sugerida = _resolve_semana_sugerida(rev_row)

    resumo_df, tok_g, eq100_g, pct_geral = _build_resumo_df(eqs, all_services, task_map, eq_label)
    setor_rows = _compute_setor_ok_counts(eqs, setor_to_services, task_map)

    group_atraso_dias = int(st.session_state.get("matriz_atraso_dias", 7) or 7)
    group_rev_start = pd.to_datetime(
        (rev_row or {}).get("data_inicio") or (rev_row or {}).get("created_at"),
        errors="coerce",
        utc=True,
    )
    if pd.isna(group_rev_start):
        from src.utils.timezone import now_utc as _now_utc
        group_rev_start = pd.Timestamp(_now_utc()).normalize()

    eq_ids = [e["id"] for e in eqs]
    svc_ids_all = [s.get("id") for s in all_services if s.get("id")]
    sector_tables_for_export = _build_sector_tables_for_export(eqs, setor_to_services, task_map, eq_label_short)
    view_agg = _build_view_agg(eqs, all_services, task_map, eq_label)

    return MatrixGroupContext(
        grupo_id=grupo_id,
        revisao_id=revisao_id,
        grupo_nome=grupo_nome,
        titulo=titulo,
        rev_row=rev_row,
        limit_eq=limit_eq,
        payload=payload,
        eqs=eqs,
        setor_to_services=setor_to_services,
        all_services=all_services,
        tarefas=tarefas,
        task_map=task_map,
        eq_ocultos_set=eq_ocultos_set,
        eq_ids=eq_ids,
        eq_label=eq_label,
        eq_label_short=eq_label_short,
        semanas_disp=semanas_disp,
        semana_sugerida=semana_sugerida,
        total_per_eq=max(len(all_services), 1) * 3,
        resumo_df=resumo_df,
        tok_g=tok_g,
        eq100_g=eq100_g,
        pct_geral=pct_geral,
        setor_rows=setor_rows,
        group_atraso_dias=group_atraso_dias,
        group_rev_start=group_rev_start,
        svc_ids_all=svc_ids_all,
        view_agg=view_agg,
        sector_tables_for_export=sector_tables_for_export,
    )
