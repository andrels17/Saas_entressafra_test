"""Dispatcher de e-mail semanal.

Orquestra todo o fluxo:
  1. Busca grupos de destinatários por departamento
  2. Para cada departamento:
     a. Carrega tarefas e calcula métricas
     b. Monta RelatorioDeptPayload
     c. Gera PDF via pdf_relatorio_semanal.build_weekly_pdf
     d. Envia e-mail via smtp_sender.send_email
  3. Para admins: envia relatório consolidado de todos os departamentos

Pode ser chamado de dois contextos:
  - Streamlit (botão manual): importa e chama dispatch_relatorio_semanal()
  - Script standalone (scheduler.py): mesmo ponto de entrada, sem st.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, TYPE_CHECKING

import pandas as pd

from src.utils.timezone import days_since_utc, semana_da_revisao

log = logging.getLogger(__name__)
logger = log


if TYPE_CHECKING:
    from src.services.reporting.pdf_relatorio_semanal import RelatorioDeptPayload


# ── helpers internos ────────────────────────────────────────────────────

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
        kdf = get_group_kpis(tenant_id, revisao_id, "0", prefer_mv=False)
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







def _build_dashboard_base(
        sb, tenant_id: str, revisao_id: str, grupo_ids: list[str],
        tarefas: list[dict] | None = None) -> tuple[pd.DataFrame, list[dict]]:
    """Monta a mesma base lógica usada no dashboard para um conjunto de grupos.

    Retorna (base_normalizada, rows_equipamentos).
    """
    from src.ui.pages.dashboard.transforms import normalize_matriz_base

    grupo_ids = [str(g) for g in (grupo_ids or []) if g]
    if not grupo_ids:
        return pd.DataFrame(), []

    # Tarefas flat da revisão — paginadas para não truncar em 1000 linhas
    if tarefas is None:
        tarefas = []
        _page_size = 1000
        _start = 0
        while True:
            _page = _with_fallback(
                lambda s=_start: (
                    sb.table("tarefas_servico")
                    .select("equipamento_id,servico_id,status,etapa_d,etapa_r,etapa_m,updated_at")
                    .eq("tenant_id", tenant_id)
                    .eq("revisao_id", revisao_id)
                    .range(s, s + _page_size - 1)
                    .execute()
                    .data
                ) or [],
                [],
                context=f"Erro ao carregar tarefas base da revisão {revisao_id} (pág {_start // _page_size})",
            )
            tarefas.extend(_page)
            if len(_page) < _page_size:
                break
            _start += _page_size

    # Equipamentos dos grupos alvo
    eq_por_grupo = _load_equipamentos_ativos(sb, tenant_id, grupo_ids)
    eq_rows: list[dict] = []
    eq_map: dict[str, dict] = {}
    for gid, eqs in (eq_por_grupo or {}).items():
        for eq in eqs or []:
            row = dict(eq)
            row["grupo_id"] = gid
            eq_rows.append(row)
            eq_map[str(eq.get("id"))] = row

    # Grupos / serviços / grupo_servicos (sem filtrar ativo, como dashboard)
    grupo_rows = _with_fallback(
        lambda: (
            sb.table("equip_grupos")
            .select("id,nome,departamento_id")
            .eq("tenant_id", tenant_id)
            .in_("id", grupo_ids)
            .execute()
            .data
        ) or [],
        [],
        context=f"Erro ao carregar grupos {grupo_ids}",
    )
    serv_rows = _with_fallback(
        lambda: (
            sb.table("servicos")
            .select("id,nome,setor")
            .eq("tenant_id", tenant_id)
            .execute()
            .data
        ) or [],
        [],
        context=f"Erro ao carregar serviços do tenant {tenant_id}",
    )
    grupo_servicos_rows = _with_fallback(
        lambda: (
            sb.table("grupo_servicos")
            .select("grupo_id,servico_id")
            .eq("tenant_id", tenant_id)
            .in_("grupo_id", grupo_ids)
            .execute()
            .data
        ) or [],
        [],
        context=f"Erro ao carregar grupo_servicos dos grupos {grupo_ids}",
    )

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
    for t in tarefas or []:
        eid = str(t.get("equipamento_id")) if t.get("equipamento_id") is not None else None
        sid = str(t.get("servico_id")) if t.get("servico_id") is not None else None
        eq = eq_map.get(eid, {})
        gid = eq.get("grupo_id")
        gid_s = str(gid) if gid is not None else None
        if not gid_s or gid_s not in grupo_map:
            continue
        grp = grupo_map.get(gid_s, {})
        svc = serv_map.get(sid, {})
        enriched = {
            "equipamento_id": t.get("equipamento_id"),
            "grupo_id": gid,
            "grupo_nome": grp.get("nome"),
            "departamento_id": grp.get("departamento_id"),
            "frota": eq.get("frota"),
            "modelo": eq.get("modelo"),
            "servico_id": t.get("servico_id"),
            "setor_nome": svc.get("setor") or "—",
            "status": t.get("status"),
            "etapa_d": t.get("etapa_d"),
            "etapa_r": t.get("etapa_r"),
            "etapa_m": t.get("etapa_m"),
            "updated_at": t.get("updated_at"),
        }
        raw_tasks.append(enriched)
        if eid and sid:
            task_map[(eid, sid)] = _merge_task(task_map.get((eid, sid)), enriched)

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

    # Fonte principal: tarefas reais da revisão.
    raw = list(task_map.values())
    eids_with_task = {
        str(r.get("equipamento_id"))
        for r in raw
        if r.get("equipamento_id") is not None
    }

    # Fallback estrutural apenas para equipamentos sem qualquer tarefa ainda.
    for eid, eq in eq_map.items():
        if eid in eids_with_task:
            continue
        gid_s = str(eq.get("grupo_id") or "")
        if not gid_s or gid_s not in grupo_map:
            continue
        grp = grupo_map.get(gid_s, {})
        service_ids = group_services.get(gid_s, [])
        if not service_ids:
            continue
        for sid in service_ids:
            svc = serv_map.get(str(sid), {})
            raw.append({
                "equipamento_id": eq.get("id"),
                "grupo_id": eq.get("grupo_id"),
                "grupo_nome": grp.get("nome"),
                "departamento_id": grp.get("departamento_id"),
                "frota": eq.get("frota"),
                "modelo": eq.get("modelo"),
                "servico_id": sid,
                "setor_nome": svc.get("setor") or "—",
                "status": "pendente",
                "etapa_d": False,
                "etapa_r": False,
                "etapa_m": False,
                "updated_at": None,
            })

    if not raw and raw_tasks:
        raw = raw_tasks

    eq_meta = [
        {
            "equipamento_id": r.get("id"),
            "frota": r.get("frota"),
            "modelo": r.get("modelo"),
            "departamento_id": grupo_map.get(str(r.get("grupo_id") or ""), {}).get("departamento_id"),
        }
        for r in eq_rows
    ]

    base = normalize_matriz_base(raw, eq_meta)
    if not base.empty and "grupo_id" in base.columns:
        base = base[base["grupo_id"].isin(grupo_ids)].copy()
    return base, eq_rows



# ── Construção do payload ───────────────────────────────────────────────



def _build_payload(
    *,
    tarefas: list[dict],
    revisao: dict,
    departamento_nome: str,
    tenant_nome: str,
    branding: dict,
    sb,
    tenant_id: str,
    grupo_ids: list[str],
    dias_travado: int = 2,
    dias_sem_update: int = 5,
):
    from src.services.reporting.pdf_relatorio_semanal import (
        RelatorioDeptPayload, SemanaSnapshot, EquipamentoCritico,
    )
    from src.ui.pages.dashboard.transforms import equipment_progress, overall_from_base, group_progress

    semanas_total = int(revisao.get("semanas_total") or 1)
    data_inicio = revisao.get("data_inicio")
    semana_atual = _semana_atual(data_inicio, semanas_total)

    # Mesmo caminho do dashboard de equipamentos para o detalhamento por frota.
    base, _eq_rows = _build_dashboard_base(
        sb, tenant_id, revisao.get("id", ""), grupo_ids, tarefas
    )

    eq_prog = equipment_progress(base) if base is not None and not base.empty else pd.DataFrame()
    overall = overall_from_base(base) if base is not None and not base.empty else {
        "pct": 0.0, "total": 0, "concl": 0, "pend": 0, "andamento": 0, "trav": 0, "na": 0
    }

    # Snapshot consolidado do kpi_engine para fallback.
    # Prioridade do PDF: usar o MESMO cálculo do dashboard sobre a base
    # normalizada; só cair no kpi_engine quando a base vier vazia/zerada.
    snapshot_kpi = _calc_snapshot_from_kpi_engine(
        tenant_id=tenant_id,
        revisao_id=revisao.get("id", ""),
        grupo_ids=grupo_ids,
    )

    dashboard_group_pct_map: dict[str, int] = {}
    if base is not None and not base.empty:
        gp_df = group_progress(base)
        if gp_df is not None and not gp_df.empty:
            dashboard_group_pct_map = {
                str(row.get("grupo_id")): int(round(float(row.get("pct_concluido") or 0)))
                for _, row in gp_df.iterrows()
                if row.get("grupo_id") is not None
            }

    group_pct_map = dashboard_group_pct_map or snapshot_kpi.get("group_pct_map") or {}

    grupo_nomes: dict[str, str] = {}
    gnrows = _with_fallback(
        lambda: (
            sb.table("equip_grupos")
            .select("id,nome")
            .in_("id", grupo_ids)
            .execute()
            .data
        ) or [],
        [],
        context=f"Erro ao carregar nomes dos grupos {grupo_ids}",
    )
    grupo_nomes = {str(r["id"]): r.get("nome") or str(r["id"]) for r in gnrows if r.get("id")}

    # Filtra as tarefas para o escopo real deste departamento.
    valid_eids: set[str] = set()
    if base is not None and not base.empty and "equipamento_id" in base.columns:
        valid_eids = {
            str(v) for v in base["equipamento_id"].dropna().astype(str).tolist()
            if str(v).strip()
        }
    elif not eq_prog.empty and "equipamento_id" in eq_prog.columns:
        valid_eids = {
            str(v) for v in eq_prog["equipamento_id"].dropna().astype(str).tolist()
            if str(v).strip()
        }

    tarefas_filtradas = []
    for t in tarefas or []:
        eid = str(t.get("equipamento_id") or "")
        if valid_eids and eid not in valid_eids:
            continue
        tarefas_filtradas.append(t)

    eq_tasks: dict[str, list[dict]] = {}
    for t in tarefas_filtradas:
        eid = str(t.get("equipamento_id") or "")
        if eid:
            eq_tasks.setdefault(eid, []).append(t)

    all_equipamentos: list[dict] = []
    criticos: list[EquipamentoCritico] = []
    n_concluidos_local = 0
    n_travados_local = 0
    n_sem_inicio_local = 0
    n_parados = 0
    n_risco_prazo = 0
    parados_detalhe: list[dict] = []

    done_steps_total_dash = 0
    expected_steps_total_dash = 0

    semana_done_steps: dict[int, int] = {}
    for t in tarefas_filtradas:
        sem = int(t.get("semana") or 0)
        if sem <= 0:
            continue
        semana_done_steps[sem] = semana_done_steps.get(sem, 0) + _sum_done_steps(t)

    if not eq_prog.empty:
        for _, row in eq_prog.iterrows():
            eid = str(row.get("equipamento_id") or "")
            grupo_id = str(row.get("grupo_id") or "")
            grupo_nome = str(row.get("grupo") or grupo_nomes.get(grupo_id) or "—")
            frota = str(row.get("Frota") or "—")
            modelo = str(row.get("Modelo") or "")
            pct = int(round(float(row.get("% Concluído") or 0)))
            expected_steps = int(row.get("expected_steps") or 0)
            done_steps = int(row.get("done_steps") or 0)

            done_steps_total_dash += done_steps
            expected_steps_total_dash += expected_steps

            tasks = eq_tasks.get(eid, [])
            done_ant = sum(
                _sum_done_steps(t) for t in tasks
                if int(t.get("semana") or 0) <= max(semana_atual - 1, 0)
            )
            pct_anterior = max(0, min(100, round(done_ant / max(expected_steps, 1) * 100))) if expected_steps > 0 else 0

            if pct >= 100 and expected_steps > 0:
                n_concluidos_local += 1

            any_travado = int(row.get("Travados") or 0) > 0 or any(
                str(t.get("status") or "").strip().lower() == "travado"
                for t in tasks
            )

            if expected_steps == 0:
                status_eq = "sem_template"
            elif any_travado:
                status_eq = "travado"
            elif done_steps == 0:
                status_eq = "zero"
            elif pct >= 100:
                status_eq = "concluido"
            else:
                status_eq = "em_andamento"

            ultima_mov = None
            ultima_semana = None
            for t in tasks:
                done_steps_task = _sum_done_steps(t)
                status_norm = str(t.get("status") or "").strip().lower()

                # `updated_at` de tarefa pendente/estrutural não conta como
                # movimentação real. Só entra como fallback quando já houve
                # atividade real no item.
                mov_ts = _best_ts(
                    t.get("dt_etapa_m"),
                    t.get("dt_etapa_r"),
                    t.get("dt_etapa_d"),
                )

                has_real_activity = (
                    done_steps_task > 0
                    or status_norm in {
                        "travado",
                        "em_andamento",
                        "em andamento",
                        "andamento",
                        "concluido",
                        "concluído",
                    }
                )

                if mov_ts is None and has_real_activity:
                    mov_ts = t.get("updated_at")

                if mov_ts and (ultima_mov is None or mov_ts > ultima_mov):
                    ultima_mov = mov_ts

                sem_t = int(t.get("semana") or 0)
                if done_steps_task > 0 and sem_t > 0 and (ultima_semana is None or sem_t > ultima_semana):
                    ultima_semana = sem_t

            dias_sem_manut = _dias_desde(ultima_mov)
            dias_sem_manut_efetivo = (
                _dias_desde(data_inicio)
                if (dias_sem_manut is None and ultima_mov is None and data_inicio)
                else dias_sem_manut
            )

            if done_steps == 0 and expected_steps > 0:
                criticos.append(EquipamentoCritico(
                    frota=frota, modelo=modelo, grupo=grupo_nome, pct=0, status="zero"
                ))
            elif any_travado:
                criticos.append(EquipamentoCritico(
                    frota=frota, modelo=modelo, grupo=grupo_nome, pct=pct, status="travado"
                ))

            if any_travado:
                n_travados_local += 1
            if done_steps == 0 and expected_steps > 0:
                n_sem_inicio_local += 1

            parado_eq = (
                expected_steps > 0 and pct < 100 and not any_travado and
                dias_sem_manut_efetivo is not None and dias_sem_manut_efetivo >= dias_sem_update
            )
            if parado_eq:
                n_parados += 1
                parados_detalhe.append({
                    "frota": frota,
                    "modelo": modelo,
                    "grupo": grupo_nome,
                    "departamento": departamento_nome,
                    "ultima_semana": ultima_semana,
                    "dias_parado": dias_sem_manut_efetivo,
                    "ultima_mov": ultima_mov,
                    "status": "Sem nenhum apontamento desde o início" if ultima_mov is None else "Sem manutenção desde a semana " + (str(ultima_semana) if ultima_semana else "inicial"),
                    "progresso": pct,
                })

            esperado_pct = _pct(semana_atual, semanas_total)
            risco_prazo_eq = bool(
                expected_steps > 0 and pct < max(esperado_pct - 15, 0) and pct < 100
            )
            if risco_prazo_eq:
                n_risco_prazo += 1

            sem_inicio_eq = bool(done_steps == 0 and expected_steps > 0)
            alerta_eq = bool(any_travado or sem_inicio_eq or parado_eq or risco_prazo_eq)

            all_equipamentos.append({
                "equipamento_id": eid,
                "frota": frota,
                "modelo": modelo,
                "grupo": grupo_nome,
                "grupo_id": grupo_id,
                "pct": pct,
                "pct_anterior": pct_anterior,
                "status": status_eq,
                "done_steps": done_steps,
                "total_steps": expected_steps,
                "ultima_mov": ultima_mov,
                "ultima_semana": ultima_semana,
                "dias_sem_manut": dias_sem_manut_efetivo,
                "grupo_pct": int(group_pct_map.get(grupo_id, pct)),
                "flag_travado": bool(any_travado),
                "flag_sem_inicio": sem_inicio_eq,
                "flag_parado": bool(parado_eq),
                "flag_risco_prazo": risco_prazo_eq,
                "flag_alerta": alerta_eq,
            })

    # Topo do relatório semanal:
    # prioriza o snapshot consolidado por grupo em RAW para alinhar com o dashboard,
    # mas mantém a base local como fallback seguro para não quebrar a geração do PDF.
    dashboard_pct = int(round(float(overall.get("pct") or 0)))
    dashboard_n_equip = int(overall.get("total") or len(all_equipamentos))

    snapshot_pct = int(snapshot_kpi.get("pct_geral") or 0)
    snapshot_n_equip = int(snapshot_kpi.get("n_equipamentos") or 0)
    snapshot_done = int(snapshot_kpi.get("done_steps_total") or 0)
    snapshot_expected = int(snapshot_kpi.get("expected_steps_total") or 0)

    use_snapshot = bool(snapshot_expected > 0)

    pct_geral_snapshot = snapshot_pct if use_snapshot else dashboard_pct
    n_equipamentos = snapshot_n_equip if use_snapshot and snapshot_n_equip > 0 else dashboard_n_equip
    done_steps_total = snapshot_done if use_snapshot else int(done_steps_total_dash)
    expected_steps_total = snapshot_expected if use_snapshot else int(expected_steps_total_dash)

    n_concluidos = max(n_concluidos_local, int(overall.get("concl") or 0))
    n_travados = max(n_travados_local, int(overall.get("trav") or 0))
    # No dashboard, "pend" é por linha/serviço, não por frota. Para não inflar e nem zerar,
    # preserva a contagem local por frota sem início.
    n_sem_inicio = n_sem_inicio_local

    evolucao: list[SemanaSnapshot] = []
    pct_semana_anterior = 0
    pct_semana_atual = pct_geral_snapshot

    if expected_steps_total > 0 or pct_geral_snapshot > 0:
        # Tendência semanal FINAL:
        # - usa a coluna `semana` de tarefas_servico
        # - considera apenas tarefas do departamento atual (tarefas_filtradas)
        # - usa SEMPRE o mesmo denominador do KPI do topo: expected_steps_total
        # - se não houver avanço na semana atual, mantém o patamar da semana anterior
        evolucao = []
        cumulative_done = 0

        for sem in range(1, max(int(semana_atual or 1), 1) + 1):
            done_sem = int(semana_done_steps.get(sem, 0) or 0)
            cumulative_done += done_sem

            pct_sem = (
                max(0, min(100, round(cumulative_done / max(expected_steps_total, 1) * 100)))
                if expected_steps_total > 0 else 0
            )

            evolucao.append(SemanaSnapshot(
                semana=sem,
                concluidos=cumulative_done,
                total=expected_steps_total,
                pct=pct_sem,
            ))

        if not evolucao:
            evolucao = [SemanaSnapshot(
                semana=max(int(semana_atual or 1), 1),
                concluidos=0,
                total=expected_steps_total,
                pct=0,
            )]

        # Sem avanço nesta semana => linha reta no último ponto.
        if len(evolucao) >= 2 and int(semana_done_steps.get(int(semana_atual or 0), 0) or 0) == 0:
            evolucao[-1] = SemanaSnapshot(
                semana=evolucao[-1].semana,
                concluidos=evolucao[-1].concluidos,
                total=evolucao[-1].total,
                pct=int(evolucao[-2].pct or 0),
            )

        # O último ponto deve bater exatamente com o KPI do topo.
        if evolucao:
            evolucao[-1] = SemanaSnapshot(
                semana=evolucao[-1].semana,
                concluidos=evolucao[-1].concluidos,
                total=evolucao[-1].total,
                pct=int(pct_geral_snapshot or evolucao[-1].pct or 0),
            )

        pct_semana_atual = int(evolucao[-1].pct or 0)
        pct_semana_anterior = int(evolucao[-2].pct or 0) if len(evolucao) >= 2 else 0

    parados_detalhe = sorted(
        parados_detalhe,
        key=lambda x: (-(x.get("dias_parado") or 0), str(x.get("frota") or "")),
    )
    all_equipamentos = sorted(
        all_equipamentos,
        key=lambda e: (-int(e.get("pct", 0) or 0), str(e.get("frota") or "")),
    )
    criticos = sorted(criticos, key=lambda x: (int(x.pct or 0), str(x.frota or "")))

    # Total de atenção padronizado por equipamento único.
    # Antes somávamos categorias (travado + parado + risco + sem início), o que
    # inflava o número quando a mesma frota aparecia em mais de uma condição.
    n_alertas_total = sum(1 for eq in all_equipamentos if bool(eq.get("flag_alerta")))

    payload = RelatorioDeptPayload(
        tenant_nome=tenant_nome or "AgroSafra",
        departamento_nome=departamento_nome,
        revisao_titulo=revisao.get("titulo") or "Revisão",
        semana_atual=semana_atual,
        semanas_total=semanas_total,
        data_inicio=data_inicio,
        pct_geral=pct_geral_snapshot,
        n_equipamentos=n_equipamentos,
        n_concluidos=n_concluidos,
        n_alertas_total=n_alertas_total,
        done_steps=done_steps_total,
        expected_steps=expected_steps_total,
        evolucao=evolucao,
        pct_semana_anterior=int(pct_semana_anterior or 0),
        pct_semana_atual=int(pct_semana_atual or 0),
        criticos=criticos,
        todos_equipamentos=all_equipamentos,
        n_travados=n_travados,
        n_sem_inicio=n_sem_inicio,
        n_parados=n_parados,
        n_risco_prazo=n_risco_prazo,
        parados_detalhe=parados_detalhe,
        primary_color=branding.get("primary_color") or "#FFD100",
        logo_url=branding.get("logo_url"),
    )
    return payload, all_equipamentos


# ── Resultado do dispatch ───────────────────────────────────────────────

@dataclass
class DispatchResult:
    total_emails: int = 0
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


# ── Função principal ────────────────────────────────────────────────────

def dispatch_relatorio_semanal(
    *,
    tenant_id: str,
    revisao_id: str,
    dias_travado: int = 2,
    dias_sem_update: int = 5,
    dry_run: bool = False,        # True = gera PDF mas não envia
    dept_ids_filter: list[str] | None = None,   # None = todos
    progress_callback=None,       # callable(msg: str) para UI
) -> DispatchResult:
    """
    Ponto de entrada principal. Pode ser chamado do Streamlit ou do scheduler.
    Se dry_run=True, retorna sem enviar mas valida tudo (útil para testes).
    """
    from src.db.supabase_client import get_supabase_service
    from src.services.email.recipients import get_recipient_groups
    from src.services.email.smtp_sender import (
        EmailMessage, send_email_with_retry, build_html_body,
        _load_config_from_secrets,
    )
    from src.services.reporting.pdf_relatorio_semanal import build_weekly_pdf

    def _log(msg: str):
        log.info(msg)
        if progress_callback:
            progress_callback(msg)

    result = DispatchResult()

    # Configuração SMTP
    try:
        smtp_cfg = _load_config_from_secrets()
    except ValueError as e:
        result.errors.append(f"SMTP não configurado: {e}")
        return result

    sb = get_supabase_service()

    # FAIL FAST — evita gastar tempo com queries pesadas quando a revisão
    # não foi informada ou não existe.
    if not revisao_id:
        result.errors.append("Revisão não informada.")
        return result

    revisao = _load_revisao(sb, revisao_id)
    if not revisao:
        result.errors.append(f"Revisão não encontrada: {revisao_id}")
        return result

    # Só carrega o restante depois de validar a revisão.
    _log(f"Revisão validada: id={revisao.get('id')} titulo={revisao.get('titulo') or '—'}")
    tenant_nome = _load_tenant_nome(sb, tenant_id)
    branding = _load_branding(sb, tenant_id)

    # ── Todos os departamentos ativos — para o relatório executivo ───────────
    from src.services.email.recipients import _build_all_dept_groups, get_manager_pdf_bundles
    all_dept_groups = _build_all_dept_groups(tenant_id)
    if dept_ids_filter:
        all_dept_groups = [
            g for g in all_dept_groups if g.departamento_id in dept_ids_filter]

    # Bundles agrupados por gestor — cada gestor recebe UM único e-mail com
    # os PDFs de TODOS os departamentos aos quais está vinculado.
    manager_bundles = get_manager_pdf_bundles(tenant_id)
    if dept_ids_filter:
        manager_bundles = [
            b for b in manager_bundles
            if any(d in dept_ids_filter for d in b.departamento_ids)
        ]

    if not manager_bundles and not all_dept_groups:
        result.skipped += 1
        result.errors.append(
            "Nenhum departamento ou grupo ativo encontrado para gerar os relatórios."
        )
        return result

    _log(f"Iniciando disparo para {len(manager_bundles)} gestor(es) (agrupado por vínculo)…")

    # Pré-carrega todas as tarefas uma única vez — evita N queries ao banco.
    tarefas_index = _load_tarefas_all(sb, tenant_id, revisao_id)

    # Cache de PDFs por ESCOPO (departamento + grupos).
    #
    # IMPORTANTE: dois gestores podem estar vinculados ao mesmo departamento,
    # mas com grupos diferentes. Se o cache usar apenas dep_id, o primeiro PDF
    # gerado para aquele departamento pode ser reaproveitado indevidamente para
    # outro gestor, fazendo o e-mail sair com grupos errados.
    pdf_cache: dict[tuple[str, tuple[str, ...]], tuple] = {}

    def _pdf_cache_key(dep_id: str, grupo_ids: list) -> tuple[str, tuple[str, ...]]:
        grupos_norm = tuple(sorted({str(g) for g in (grupo_ids or []) if g}))
        return str(dep_id), grupos_norm

    def _get_or_build_pdf(dep_id: str, dep_nome: str, grupo_ids: list):
        cache_key = _pdf_cache_key(dep_id, grupo_ids)
        if cache_key in pdf_cache:
            return pdf_cache[cache_key]
        try:
            tarefas = _load_tarefas(
                sb, tenant_id, revisao_id, grupo_ids,
                _tarefas_index=tarefas_index,
            )
            payload, _ = _build_payload(
                tarefas=tarefas,
                revisao=revisao,
                departamento_nome=dep_nome,
                tenant_nome=tenant_nome,
                branding=branding,
                sb=sb,
                tenant_id=tenant_id,
                grupo_ids=grupo_ids,
                dias_travado=dias_travado,
                dias_sem_update=dias_sem_update,
            )
            pdf_b = build_weekly_pdf(payload)
            pdf_n = (
                f"relatorio_{dep_nome.lower().replace(' ', '_')}"
                f"_semana{payload.semana_atual}.pdf"
            )
            try:
                from src.services.reporting.pdf_validator import (
                    validate_pdf as _vp, PdfValidationError as _VPErr)
                _vp(pdf_b, context=f"relatorio_semanal.{dep_nome[:30]}")
            except ImportError:
                pass
            pdf_cache[cache_key] = (pdf_b, pdf_n, payload)
            return pdf_cache[cache_key]
        except Exception as exc:
            _log(f"  ❌ Erro ao gerar PDF de {dep_nome}: {exc}")
            return None

    for bundle in manager_bundles:
        rec = bundle.recipient
        _log(f"  → Gestor: {rec.nome} <{rec.email}> — {len(bundle.departamento_ids)} depto(s): {', '.join(bundle.departamento_nomes)}")

        dep_ids_ef = bundle.departamento_ids
        dep_nomes_ef = bundle.departamento_nomes
        if dept_ids_filter:
            pairs = [(d, n) for d, n in zip(dep_ids_ef, dep_nomes_ef) if d in dept_ids_filter]
            dep_ids_ef, dep_nomes_ef = (list(x) for x in zip(*pairs)) if pairs else ([], [])

        if not dep_ids_ef:
            _log("    ↳ Nenhum departamento no filtro — pulando.")
            continue

        attachments: list = []
        payloads_bundle: list = []
        for dep_id, dep_nome in zip(dep_ids_ef, dep_nomes_ef):
            grupo_ids = bundle.grupo_ids_por_dept.get(dep_id, [])
            built = _get_or_build_pdf(dep_id, dep_nome, grupo_ids)
            if built is None:
                result.failed += 1
                result.errors.append(f"PDF inválido para {dep_nome} — não incluído no e-mail de {rec.email}")
                continue
            pdf_b, pdf_n, pay = built
            attachments.append((pdf_b, pdf_n))
            payloads_bundle.append(pay)

        if not attachments:
            _log("    ↳ Nenhum PDF válido gerado — pulando gestor.")
            continue

        result.total_emails += 1
        _log(f"    ↳ Enviando {len(attachments)} PDF(s) para {rec.email}")

        if dry_run:
            _log("    ↳ [DRY RUN] — e-mail não enviado.")
            result.sent += 1
            continue

        p0 = payloads_bundle[0]
        n_trav_b  = sum(getattr(p, "n_travados",   0) for p in payloads_bundle)
        n_sem_b   = sum(getattr(p, "n_sem_inicio",  0) for p in payloads_bundle)
        n_par_b   = sum(getattr(p, "n_parados",     0) for p in payloads_bundle)
        n_risc_b  = sum(getattr(p, "n_risco_prazo", 0) for p in payloads_bundle)
        n_equip_b = sum(getattr(p, "n_equipamentos", 0) for p in payloads_bundle)
        done_steps_bundle = sum(int(getattr(p, "done_steps", 0) or 0) for p in payloads_bundle)
        expected_steps_bundle = sum(int(getattr(p, "expected_steps", 0) or 0) for p in payloads_bundle)
        pct_medio = (
            max(0, min(100, round(done_steps_bundle / max(expected_steps_bundle, 1) * 100)))
            if expected_steps_bundle > 0
            else round(sum(getattr(p, "pct_geral", 0) for p in payloads_bundle) / len(payloads_bundle))
        )
        dept_label = dep_nomes_ef[0] if len(dep_nomes_ef) == 1 else ", ".join(dep_nomes_ef)
        top_par_b  = sorted(
            [item for p in payloads_bundle for item in (p.parados_detalhe or [])],
            key=lambda x: (-(int(x.get("dias_parado") or 0)), str(x.get("frota") or ""))
        )[:10]
        max_dias_b = max([int(x.get("dias_parado") or 0) for x in top_par_b] or [0])

        try:
            html = build_html_body(
                destinatario_nome=rec.nome,
                departamento_nome=dept_label,
                revisao_titulo=p0.revisao_titulo,
                semana_atual=p0.semana_atual,
                semanas_total=p0.semanas_total,
                pct_geral=pct_medio,
                n_alertas=n_trav_b + n_sem_b + n_par_b + n_risc_b,
                primary_color=p0.primary_color,
                n_travados=n_trav_b,
                n_sem_inicio=n_sem_b,
                n_parados=n_par_b,
                n_risco_prazo=n_risc_b,
                total_equipamentos=n_equip_b,
                max_dias_parado=max_dias_b,
                top_parados=top_par_b,
            )
            subject = (
                f"[{p0.revisao_titulo}] Relatório Semanal — "
                f"{dept_label} · Semana {p0.semana_atual}"
            )
            send_email_with_retry(EmailMessage(
                to=[rec.email],
                subject=subject,
                html_body=html,
                attachments=attachments,
            ), cfg=smtp_cfg,
                on_retry=lambda attempt, exc: _log(f"    ↳ ⚠️ Retry {attempt}: {exc}"))
            result.sent += 1
            _log("    ↳ ✅ Enviado.")
        except Exception as e:
            result.failed += 1
            msg = f"Falha ao enviar para {rec.email}: {e}"
            result.errors.append(msg)
            _log(f"    ↳ ❌ {msg}")
            try:
                from src.services.email.dead_letter import enqueue_failed
                for pdf_b, pdf_fn in attachments:
                    enqueue_failed(
                        tenant_id=tenant_id,
                        revisao_id=revisao_id,
                        recipient=rec.email,
                        subject=subject,
                        html_body=html,
                        pdf_bytes=pdf_b,
                        pdf_filename=pdf_fn,
                        error=str(e),
                    )
            except Exception as dl_exc:
                import logging
                logging.getLogger("saas.email.dispatcher").error(
                    "dispatcher | falha ao enfileirar no dead-letter para %s "
                    "(email %r será perdido): %s",
                    rec.email, subject, dl_exc
                )

    # ── Relatório executivo para supervisores/admins ────────────────────────
    _log("  → Gerando relatório executivo para supervisores…")
    try:
        from src.services.email.recipients import get_executive_recipients
        from src.services.reporting.pdf_relatorio_executivo import (
            build_executive_pdf, RelatorioExecutivoPayload, DeptSnapshot,
        )

        exec_recs = get_executive_recipients(tenant_id)
        if exec_recs:
            # Mapa de percentuais por departamento alinhado à lógica do dashboard:
            # usa KPIs de grupo calculados do RAW (prefer_mv=False) e agrega
            # done_steps/expected_steps por departamento, exatamente como a UI.
            exec_dept_pct_map: dict[str, int] = {}
            try:
                from src.utils.kpi_engine import get_group_kpis

                gid_to_exec_dept: dict[str, str] = {}
                for _g in all_dept_groups:
                    dep_id_s = str(_g.departamento_id) if _g.departamento_id is not None else None
                    for _gid in (_g.grupo_ids or []):
                        if dep_id_s:
                            gid_to_exec_dept[str(_gid)] = dep_id_s

                kpi_exec_df = get_group_kpis(
                    tenant_id,
                    revisao_id,
                    ver="email_exec_dashboard",
                    prefer_mv=False,
                )

                if kpi_exec_df is not None and not kpi_exec_df.empty:
                    _tmp = kpi_exec_df.copy()
                    _tmp["grupo_id"] = _tmp["grupo_id"].map(lambda v: str(v) if pd.notna(v) else None)
                    _tmp["departamento_id"] = _tmp["grupo_id"].map(
                        lambda v: gid_to_exec_dept.get(str(v)) if v is not None else None
                    )
                    _tmp = _tmp.dropna(subset=["departamento_id"]).copy()
                    if not _tmp.empty:
                        _tmp["done_steps"] = pd.to_numeric(_tmp.get("done_steps", 0), errors="coerce").fillna(0)
                        _tmp["expected_steps"] = pd.to_numeric(_tmp.get("expected_steps", 0), errors="coerce").fillna(0)
                        _agg = (
                            _tmp.groupby("departamento_id", dropna=True)
                            .agg(done_steps=("done_steps", "sum"), expected_steps=("expected_steps", "sum"))
                            .reset_index()
                        )
                        _agg = _agg[_agg["expected_steps"] > 0].copy()
                        exec_dept_pct_map = {
                            str(row["departamento_id"]): int(round((float(row["done_steps"]) / max(float(row["expected_steps"]), 1)) * 100))
                            for _, row in _agg.iterrows()
                        }
            except Exception as exec_kpi_exc:
                _log(f"    ↳ Aviso: não foi possível alinhar executivo ao dashboard por KPI raw: {exec_kpi_exc}")

            # Constrói DeptSnapshot para cada grupo de departamento já
            # processado
            dept_snapshots: list[DeptSnapshot] = []
            payloads_por_departamento: list["RelatorioDeptPayload"] = []
            sem_atual_rev = _semana_atual(
                revisao.get("data_inicio"), int(
                    revisao.get("semanas_total") or 1))
            trend_acc: dict[int, dict[str, int]] = {}
            heatmap_semanal: list[dict] = []
            alertas_parados = {"atencao": 0, "critico": 0, "urgente": 0}

            # Executivo: usa a MESMA lógica do semanal.
            # Passa todas as tarefas da revisão para _build_payload e deixa
            # o filtro real acontecer lá dentro, via grupo_ids.
            tarefas_all = []
            for _tasks in (tarefas_index or {}).values():
                tarefas_all.extend(_tasks or [])

            for grp in all_dept_groups:  # TODOS os deptos, não só os com gestores
                try:
                    p, eq_list_g = _build_payload(
                        tarefas=tarefas_all,
                        revisao=revisao,
                        departamento_nome=grp.departamento_nome,
                        tenant_nome=tenant_nome,
                        branding=branding,
                        sb=sb,
                        tenant_id=tenant_id,
                        grupo_ids=grp.grupo_ids,
                        dias_travado=dias_travado,
                        dias_sem_update=dias_sem_update,
                    )

                    # Só entra no executivo se o departamento realmente tiver
                    # equipamentos no escopo dele.
                    todos = p.todos_equipamentos or []
                    if not todos:
                        continue

                    # Não deixa entrar depto desativado/sem atividade por engano:
                    # exige pelo menos 1 equipamento com passos esperados.
                    if not any(int(e.get("total_steps", 0) or 0) > 0 for e in todos):
                        continue

                    candidatos_validos = [
                        e for e in todos
                        if int(e.get("total_steps", 0) or 0) > 0
                    ]

                    top_criticos = sorted(
                        [
                            e for e in candidatos_validos
                            if int(e.get("pct", 0) or 0) < 100
                        ],
                        key=lambda e: (
                            int(e.get("pct", 0) or 0),
                            str(e.get("frota") or "")
                        )
                    )[:3]

                    top_melhores = sorted(
                        [
                            e for e in candidatos_validos
                            if 0 < int(e.get("pct", 0) or 0) < 100
                        ],
                        key=lambda e: (
                            -int(e.get("pct", 0) or 0),
                            str(e.get("frota") or "")
                        )
                    )[:3]

                    maiores_evolucoes = sorted(
                        [
                            e for e in candidatos_validos
                            if (int(e.get("pct", 0) or 0) - int(e.get("pct_anterior", 0) or 0)) > 0
                        ],
                        key=lambda e: (
                            -(int(e.get("pct", 0) or 0) - int(e.get("pct_anterior", 0) or 0)),
                            -int(e.get("pct", 0) or 0),
                            str(e.get("frota") or "")
                        )
                    )[:3]

                    payloads_por_departamento.append(p)

                    # Percentual do departamento alinhado ao dashboard:
                    # agrega KPIs de grupo calculados do raw por departamento.
                    # Fallback: usa o payload semanal já calculado.
                    dept_pct_dashboard = int(
                        exec_dept_pct_map.get(str(grp.departamento_id), int(p.pct_geral or 0))
                    )
                    dept_pct_anterior_dashboard = int(p.pct_semana_anterior or 0)
                    if p.evolucao and len(p.evolucao) >= 2:
                        try:
                            dept_pct_anterior_dashboard = int(p.evolucao[-2].pct or dept_pct_anterior_dashboard)
                        except Exception:
                            dept_pct_anterior_dashboard = int(p.pct_semana_anterior or 0)

                    dept_snapshots.append(DeptSnapshot(
                        nome=grp.departamento_nome,
                        pct_geral=dept_pct_dashboard,
                        pct_anterior=dept_pct_anterior_dashboard,
                        n_equipamentos=p.n_equipamentos,
                        n_concluidos=p.n_concluidos,
                        n_travados=p.n_travados,
                        n_sem_inicio=p.n_sem_inicio,
                        n_risco_prazo=p.n_risco_prazo,
                        n_alertas_total=p.n_alertas_total,
                        top_criticos=top_criticos,
                        top_melhores=top_melhores,
                        maiores_evolucoes=maiores_evolucoes,
                        n_parados=p.n_parados,
                        max_dias_parado=max([int(x.get("dias_parado") or 0) for x in (p.parados_detalhe or [])] or [0]),
                        _done_steps=p.done_steps,
                        _expected_steps=p.expected_steps,
                    ))

                    # Tendência global executiva: agrega o acumulado por semana.
                    # Heatmap: usa o avanço da semana sobre o total esperado do departamento.
                    evolucao_sorted = sorted(p.evolucao or [], key=lambda w: getattr(w, "semana", 0))
                    prev_done = 0
                    dept_expected_total = int(getattr(p, "expected_steps", 0) or 0)

                    for wk in evolucao_sorted:
                        sem = int(getattr(wk, "semana", 0) or 0)
                        if sem <= 0:
                            continue

                        wk_done = int(getattr(wk, "concluidos", 0) or 0)
                        acc = trend_acc.setdefault(sem, {"done": 0, "total": 0})
                        acc["done"] += wk_done
                        acc["total"] += dept_expected_total

                        delta_done = max(0, wk_done - prev_done)
                        pct_incremental = (
                            max(0, min(100, round(delta_done / max(dept_expected_total, 1) * 100)))
                            if dept_expected_total > 0 else 0
                        )

                        heatmap_semanal.append({
                            "departamento": grp.departamento_nome,
                            "semana": sem,
                            "pct": pct_incremental,
                        })
                        prev_done = wk_done

                    for par in (p.parados_detalhe or []):
                        dias = int(par.get("dias_parado") or 0)
                        if dias > 21:
                            alertas_parados["urgente"] += 1
                        elif dias > 14:
                            alertas_parados["critico"] += 1
                        elif dias > 7:
                            alertas_parados["atencao"] += 1
                except Exception as e_g:
                    _log(
                        f"    ↳ Aviso: erro ao montar snapshot de {grp.departamento_nome}: {e_g}")

            if dept_snapshots:
                # Consolidado executivo ponderado pelo total esperado de etapas.
                # Isso mantém o e-mail executivo alinhado ao KPI real do dashboard
                # e evita distorções de média simples entre departamentos com pesos diferentes.
                done_steps_exec = sum(int(getattr(d, "_done_steps", 0) or 0) for d in dept_snapshots)
                expected_steps_exec = sum(int(getattr(d, "_expected_steps", 0) or 0) for d in dept_snapshots)
                pct_global = (
                    max(0, min(100, round(done_steps_exec / max(expected_steps_exec, 1) * 100)))
                    if expected_steps_exec > 0
                    else round(sum(int(d.pct_geral or 0) for d in dept_snapshots) / max(len(dept_snapshots), 1))
                )
                n_equip_total = sum(d.n_equipamentos for d in dept_snapshots)
                n_equip_concl = sum(d.n_concluidos for d in dept_snapshots)
                # Também no executivo o total representa equipamentos únicos
                # com algum ponto de atenção dentro de cada departamento.
                n_alertas_total = sum(getattr(d, "n_alertas_total", 0) for d in dept_snapshots)

                trend_semanal = []
                semanas_exec = sorted({
                    int(getattr(w, "semana", 0) or 0)
                    for p in payloads_por_departamento
                    for w in (p.evolucao or [])
                    if int(getattr(w, "semana", 0) or 0) > 0
                })
                for sem in semanas_exec:
                    done_sem = 0
                    total_sem = 0
                    for p in payloads_por_departamento:
                        expected_dep = int(getattr(p, "expected_steps", 0) or 0)
                        if expected_dep <= 0:
                            continue
                        wk = next(
                            (w for w in (p.evolucao or []) if int(getattr(w, "semana", 0) or 0) == sem),
                            None,
                        )
                        if wk is None:
                            continue
                        done_sem += int(getattr(wk, "concluidos", 0) or 0)
                        total_sem += expected_dep
                    if total_sem <= 0:
                        continue
                    trend_semanal.append({
                        "semana": sem,
                        "pct": max(0, min(100, round(done_sem / max(total_sem, 1) * 100))),
                    })
                if trend_semanal:
                    trend_semanal[-1]["pct"] = int(pct_global or trend_semanal[-1]["pct"] or 0)
                trend_semanal = trend_semanal[-4:]

                exec_payload = RelatorioExecutivoPayload(
                    tenant_nome=tenant_nome or "AgroSafra",
                    revisao_titulo=revisao.get("titulo") or "Revisão",
                    semana_atual=sem_atual_rev,
                    semanas_total=int(revisao.get("semanas_total") or 1),
                    pct_global=pct_global,
                    n_equip_total=n_equip_total,
                    n_equip_concluidos=n_equip_concl,
                    n_alertas_total=n_alertas_total,
                    departamentos=dept_snapshots,
                    primary_color=branding.get("primary_color") or "#FFD100",
                    logo_url=branding.get("logo_url"),
                    trend_semanal=trend_semanal,
                    heatmap_semanal=heatmap_semanal,
                    alertas_parados=alertas_parados,
                    top_parados=sorted([
                        item
                        for p in payloads_por_departamento
                        for item in (p.parados_detalhe or [])
                    ], key=lambda x: (-(int(x.get("dias_parado") or 0)), str(x.get("frota") or "")))[:10],
                )
                pdf_exec = build_executive_pdf(exec_payload)
                pdf_name_e = f"relatorio_executivo_semana{sem_atual_rev}.pdf"

                # ── Executivo: um e-mail único com todos os supervisores ──
                if exec_recs:
                    result.total_emails += 1
                    all_exec_emails = [rec.email for rec in exec_recs]
                    _log(f"    ↳ Executivo → {len(all_exec_emails)} destinatário(s): {', '.join(all_exec_emails)}")
                    if dry_run:
                        _log("    ↳ [DRY RUN] — e-mail não enviado.")
                        result.sent += 1
                    else:
                        try:
                            from src.services.email.smtp_sender import build_html_body, EmailMessage, send_email_with_retry
                            nomes_exec = ", ".join(rec.nome.split()[0] for rec in exec_recs)
                            saudacao_exec = nomes_exec if len(exec_recs) == 1 else "equipe de supervisão"
                            # Calcula breakdown consolidado dos departamentos
                            n_trav_exec = sum(getattr(d, "n_travados", 0) for d in dept_snapshots)
                            n_sem_exec = sum(getattr(d, "n_sem_inicio", 0) for d in dept_snapshots)
                            n_par_exec = sum(getattr(d, "n_parados", 0) for d in dept_snapshots)
                            n_risc_exec = sum(getattr(d, "n_risco_prazo", 0) for d in dept_snapshots)
                            max_dias_par_exec = max([int(getattr(d, "max_dias_parado", 0) or 0) for d in dept_snapshots] or [0])
                            html_e = build_html_body(
                                destinatario_nome="",
                                departamento_nome="Visão geral — todos os departamentos",
                                revisao_titulo=exec_payload.revisao_titulo,
                                semana_atual=exec_payload.semana_atual,
                                semanas_total=exec_payload.semanas_total,
                                pct_geral=exec_payload.pct_global,
                                n_alertas=exec_payload.n_alertas_total,
                                primary_color=exec_payload.primary_color,
                                n_travados=n_trav_exec,
                                n_sem_inicio=n_sem_exec,
                                n_parados=n_par_exec,
                                n_risco_prazo=n_risc_exec,
                                total_equipamentos=exec_payload.n_equip_total,
                                max_dias_parado=max_dias_par_exec,
                                top_parados=sorted([
                                    item
                                    for p in payloads_por_departamento
                                    for item in (p.parados_detalhe or [])
                                ], key=lambda x: (-(int(x.get("dias_parado") or 0)), str(x.get("frota") or "")))[:10],
                            )
                            send_email_with_retry(EmailMessage(
                                to=all_exec_emails,
                                subject=(
                             f"{exec_payload.revisao_titulo} - Relatório Semanal - "
                             f"Semana {exec_payload.semana_atual}/{exec_payload.semanas_total}"),
                                html_body=html_e,
                                attachments=[(pdf_exec, pdf_name_e)],
                            ), cfg=smtp_cfg,
                                on_retry=lambda attempt, exc: _log(f"    ↳ ⚠️ Retry {attempt}: {exc}"))
                            result.sent += 1
                            _log("    ↳ ✅ Executivo enviado.")
                        except Exception as e_send:
                            result.failed += 1
                            msg = f"Falha ao enviar executivo: {e_send}"
                            result.errors.append(msg)
                            _log(f"    ↳ ❌ {msg}")
            else:
                _log("    ↳ Sem dados para executivo — pulando.")
        else:
            _log(
                "    ↳ Nenhum supervisor/admin com e-mail — relatório executivo não enviado.")
    except Exception as e_exec:
        _log(f"  ⚠️ Erro ao gerar executivo: {e_exec}")

    _log(
        f"Concluído: {result.sent} enviados, {result.failed} falhas, {result.skipped} pulados.")
    return result
