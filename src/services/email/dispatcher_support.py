"""Helpers compartilhados do dispatcher de e-mail semanal.

Extraído do dispatcher principal para reduzir acoplamento e facilitar teste
unitário das rotinas de carregamento/cálculo auxiliares.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

import pandas as pd

from src.utils.timezone import days_since_utc, semana_da_revisao

log = logging.getLogger(__name__)


def _with_fallback(action: Callable[[], Any], default: Any, *, context: str):
    """Executa operações opcionais/sujeitas a falha com log consistente."""
    try:
        return action()
    except Exception as exc:  # integrações externas variam por driver/ambiente
        log.warning("%s: %s", context, exc)
        return default


def _pct(done: int, total: int) -> int:
    return round((done / max(total, 1)) * 100)


def _is_done(value: Any) -> bool:
    """Normaliza flags de etapa vindas do banco (bool/int/str)."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        s = value.strip().lower()
        return s in {"1", "true", "t", "sim", "s", "y", "yes", "ok", "x"}
    return bool(value)


def _sum_done_steps(task: dict) -> int:
    return (
        int(_is_done(task.get("etapa_d")))
        + int(_is_done(task.get("etapa_r")))
        + int(_is_done(task.get("etapa_m")))
    )


def _best_ts(*values: str | None) -> str | None:
    vals = [v for v in values if v]
    if not vals:
        return None
    return max(vals)


def _dias_to_label(dias: int | None) -> str:
    if dias is None:
        return "—"
    return f"{dias} dia" if dias == 1 else f"{dias} dias"


def _dias_desde(ts_str: str | None) -> int | None:
    """Delega ao utilitário central — garante fuso consistente (UTC)."""
    return days_since_utc(ts_str)


def _semana_atual(data_inicio_str: str | None, semanas_total: int) -> int:
    """Delega ao utilitário central — usa BRT para alinhar com o calendário do usuário."""
    return semana_da_revisao(data_inicio_str, semanas_total)


# ── Carregamento de dados ───────────────────────────────────────────────

def _load_tarefas_all(
        sb, tenant_id: str, revisao_id: str) -> dict[str, list[dict]]:
    """Carrega TODAS as tarefas da revisão de uma vez e indexa por equipamento_id.

    Não usa JOIN com equipamentos para evitar bloqueio de RLS scope-restritivo.
    A resolução grupo_id é feita em build_dept_payload via eid_to_info (RPC).
    Retorna: {equipamento_id: [tarefa, ...]}

    Usa paginação para contornar o limite padrão de 1000 linhas do Supabase.
    Sem isso, revisões com muitos equipamentos × serviços retornam tarefas
    incompletas, fazendo com que equipamentos com etapa_d/r/m = True apareçam
    como 0% no PDF (mesmo que o kpi_engine — que também pagina — mostre o
    percentual correto no cabeçalho do grupo).
    """
    all_rows: list[dict] = []
    page_size = 1000
    start = 0
    while True:
        rows = _with_fallback(
            lambda s=start: (
                sb.table("tarefas_servico")
                .select(
                    "id,equipamento_id,servico_id,status,semana,"
                    "etapa_d,etapa_r,etapa_m,observacao,updated_at,"
                    "dt_etapa_d,dt_etapa_r,dt_etapa_m"
                )
                .eq("tenant_id", tenant_id)
                .eq("revisao_id", revisao_id)
                .range(s, s + page_size - 1)
                .execute()
                .data
            ) or [],
            [],
            context=f"Erro ao pré-carregar tarefas da revisão {revisao_id} (pág {start // page_size})",
        )
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        start += page_size

    index: dict[str, list[dict]] = {}
    for t in all_rows:
        eid = t.get("equipamento_id")
        if eid:
            index.setdefault(str(eid), []).append(t)
    return index


def _load_tarefas(
        sb, tenant_id: str, revisao_id: str,
        grupo_ids: list[str],
        _tarefas_index: "dict[str, list[dict]] | None" = None,
) -> list[dict]:
    """Retorna tarefas para os grupo_ids fornecidos.

    Se _tarefas_index for passado (pré-carregado via _load_tarefas_all),
    apenas fatia em memória — sem nova query ao banco.
    Se omitido, executa a query diretamente (compatibilidade retroativa).
    """
    if not grupo_ids:
        return []

    if _tarefas_index is not None:
        # _tarefas_index é indexado por equipamento_id.
        # Retorna todas as tarefas — o filtro real por grupo_id acontece
        # dentro de _build_dashboard_base via eq_map (que é restrito aos
        # grupo_ids do departamento). Retornar tudo é seguro porque
        # _build_dashboard_base ignora tarefas cujo equipamento_id não
        # está em eq_map, que só contém equipamentos dos grupos alvo.
        out: list[dict] = []
        for tasks in _tarefas_index.values():
            out.extend(tasks)
        return out

    # Fallback: query direta sem JOIN (evita RLS em equipamentos) — paginada
    rows: list[dict] = []
    _page_size = 1000
    _start = 0
    while True:
        _page = _with_fallback(
            lambda s=_start: (
                sb.table("tarefas_servico")
                .select(
                    "id,equipamento_id,servico_id,status,semana,"
                    "etapa_d,etapa_r,etapa_m,observacao,updated_at,"
                    "dt_etapa_d,dt_etapa_r,dt_etapa_m"
                )
                .eq("tenant_id", tenant_id)
                .eq("revisao_id", revisao_id)
                .range(s, s + _page_size - 1)
                .execute()
                .data
            ) or [],
            [],
            context=f"Erro ao carregar tarefas da revisão {revisao_id} (pág {_start // _page_size})",
        )
        rows.extend(_page)
        if len(_page) < _page_size:
            break
        _start += _page_size
    return rows


def _load_grupo_template(
        sb, tenant_id: str, grupo_ids: list[str]) -> dict[str, int]:
    """Retorna {grupo_id: svc_count} — número de serviços do template por grupo.
    Mesma fonte usada pela Matriz para calcular o denominador correto.
    """
    if not grupo_ids:
        return {}
    rows = _with_fallback(
        lambda: (
            sb.table("grupo_servicos")
            .select("grupo_id,servico_id")
            .eq("tenant_id", tenant_id)
            .in_("grupo_id", grupo_ids)
            .execute()
            .data
        ) or [],
        [],
        context=f"Erro ao carregar template dos grupos {grupo_ids}",
    )
    svc_map: dict[str, set] = {}
    for r in rows:
        gid = r.get("grupo_id")
        sid = r.get("servico_id")
        if gid and sid:
            svc_map.setdefault(gid, set()).add(sid)
    return {gid: len(svcs) for gid, svcs in svc_map.items()}


def _load_equipamentos_ativos(
        sb, tenant_id: str, grupo_ids: list[str]) -> dict[str, list[dict]]:
    """Retorna {grupo_id: [{id, frota, modelo}]} — equipamentos ativos por grupo.

    Usa RPC get_equipamentos_dashboard (SECURITY DEFINER) para contornar RLS
    scope-restritivo que bloqueia SELECT geral na tabela equipamentos.
    """
    if not grupo_ids:
        return {}

    grupo_set = set(str(g) for g in grupo_ids)

    # Tenta via RPC primeiro (bypassa RLS)
    rows = []
    try:
        rpc_result = sb.rpc(
            "get_equipamentos_dashboard",
            {"p_tenant_id": tenant_id}
        ).execute()
        all_rows = rpc_result.data or []
        rows = [r for r in all_rows if str(r.get("grupo_id") or "") in grupo_set]
    except Exception:
        pass

    # Fallback: query direta
    if not rows:
        rows = _with_fallback(
            lambda: (
                sb.table("equipamentos")
                .select("id,frota,modelo,grupo_id")
                .eq("tenant_id", tenant_id)
                .is_("ativo", "true")
                .in_("grupo_id", grupo_ids)
                .execute()
                .data
            ) or [],
            [],
            context=f"Erro ao carregar equipamentos ativos dos grupos {grupo_ids}",
        )

    out: dict[str, list] = {}
    for r in rows:
        gid = r.get("grupo_id")
        if gid:
            out.setdefault(str(gid), []).append(r)
    return out


def _load_revisao(sb, revisao_id: str) -> dict:
    rows = _with_fallback(
        lambda: (
            sb.table("revisoes")
            .select("id,titulo,data_inicio,semanas_total,status")
            .eq("id", revisao_id)
            .limit(1)
            .execute()
            .data
        ),
        [],
        context=f"Erro ao carregar revisão {revisao_id}",
    )
    return rows[0] if rows else {}


def _load_tenant_nome(sb, tenant_id: str) -> str:
    rows = _with_fallback(
        lambda: (
            sb.table("tenants")
            .select("nome")
            .eq("id", tenant_id)
            .limit(1)
            .execute()
            .data
        ),
        [],
        context=f"Erro ao carregar tenant {tenant_id}",
    )
    return (rows[0].get("nome") or "") if rows else ""


def _load_branding(sb, tenant_id: str) -> dict:
    rows = _with_fallback(
        lambda: (
            sb.table("tenant_branding")
            .select("primary_color,logo_url")
            .eq("tenant_id", tenant_id)
            .limit(1)
            .execute()
            .data
        ),
        [],
        context=f"Erro ao carregar branding do tenant {tenant_id}",
    )
    return rows[0] if rows else {}


def _calc_snapshot_from_kpi_engine(
    *,
    tenant_id: str,
    revisao_id: str,
    grupo_ids: list[str],
) -> dict:
    """Snapshot consolidado por grupo usando o kpi_engine.

    Usa o motor consolidado para alinhar os números do topo do PDF
    com Home/Dashboard. O detalhamento por frota continua vindo do
    mesmo pipeline do dashboard de equipamentos.
    """
    try:
        from src.utils.kpi_engine import get_group_kpis
        kdf = get_group_kpis(tenant_id, revisao_id, "0", prefer_mv=True)
        if kdf is None or getattr(kdf, "empty", True):
            return {}

        gids = {str(g) for g in (grupo_ids or []) if g}
        if not gids:
            return {}

        gid_col = "grupo_id" if "grupo_id" in kdf.columns else None
        if gid_col is None:
            return {}

        kdf = kdf.copy()
        kdf[gid_col] = kdf[gid_col].astype(str)
        kdf = kdf[kdf[gid_col].isin(gids)].copy()
        if kdf.empty:
            return {}

        for col in ("pct", "done_steps", "expected_steps", "eq_count"):
            if col in kdf.columns:
                kdf[col] = pd.to_numeric(kdf[col], errors="coerce").fillna(0)
            else:
                kdf[col] = 0

        done = int(kdf["done_steps"].sum())
        expected = int(kdf["expected_steps"].sum())
        pct = max(0, min(100, round(done / max(expected, 1) * 100))) if expected > 0 else 0
        n_equip = int(kdf["eq_count"].sum()) if "eq_count" in kdf.columns else 0
        group_pct_map = {
            str(row[gid_col]): int(round(float(row.get("pct", 0) or 0)))
            for _, row in kdf.iterrows()
        }
        return {
            "pct_geral": pct,
            "done_steps_total": done,
            "expected_steps_total": expected,
            "n_equipamentos": n_equip,
            "group_pct_map": group_pct_map,
        }
    except Exception:
        return {}
