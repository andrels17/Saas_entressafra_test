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
from typing import Any, Callable


from src.utils.timezone import days_since_utc, semana_da_revisao

log = logging.getLogger(__name__)


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
    """
    rows = _with_fallback(
        lambda: (
            sb.table("tarefas_servico")
            .select(
                "id,equipamento_id,servico_id,status,semana,"
                "etapa_d,etapa_r,etapa_m,observacao,updated_at,"
                "dt_etapa_d,dt_etapa_r,dt_etapa_m"
            )
            .eq("tenant_id", tenant_id)
            .eq("revisao_id", revisao_id)
            .execute()
            .data
        ) or [],
        [],
        context=f"Erro ao pré-carregar tarefas da revisão {revisao_id}",
    )
    index: dict[str, list[dict]] = {}
    for t in rows:
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
        # _tarefas_index is now keyed by equipamento_id (not grupo_id)
        # Return ALL tasks - caller filters by eid via eid_to_info
        out: list[dict] = []
        for tasks in _tarefas_index.values():
            out.extend(tasks)
        return out

    # Fallback: query direta sem JOIN (evita RLS em equipamentos)
    rows = _with_fallback(
        lambda: (
            sb.table("tarefas_servico")
            .select(
                "id,equipamento_id,servico_id,status,semana,"
                "etapa_d,etapa_r,etapa_m,observacao,updated_at,"
                "dt_etapa_d,dt_etapa_r,dt_etapa_m"
            )
            .eq("tenant_id", tenant_id)
            .eq("revisao_id", revisao_id)
            .execute()
            .data
        ) or [],
        [],
        context=f"Erro ao carregar tarefas da revisão {revisao_id}",
    )
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


# ── Construção do payload ───────────────────────────────────────────────

def _merge_task_rows(prev: dict | None, cur: dict) -> dict:
    """Merge de linhas duplicadas de tarefa por (equipamento, serviço)."""
    if not prev:
        return dict(cur)
    merged = dict(prev)
    for etapa_col in ("etapa_d", "etapa_r", "etapa_m"):
        merged[etapa_col] = _is_done(prev.get(etapa_col)) or _is_done(cur.get(etapa_col))
    status_order = {
        "nao_aplica": 0,
        "não aplica": 0,
        "nao aplica": 0,
        "pendente": 1,
        "travado": 2,
        "em_andamento": 3,
        "em andamento": 3,
        "andamento": 3,
        "concluido": 4,
        "concluído": 4,
    }
    prev_status = str(prev.get("status") or "").strip().lower()
    cur_status = str(cur.get("status") or "").strip().lower()
    if status_order.get(cur_status, -1) >= status_order.get(prev_status, -1):
        merged["status"] = cur.get("status")
    prev_upd = str(prev.get("updated_at") or "")
    cur_upd = str(cur.get("updated_at") or "")
    if cur_upd >= prev_upd:
        merged["updated_at"] = cur.get("updated_at")
        for k in ("dt_etapa_d", "dt_etapa_r", "dt_etapa_m", "semana"):
            if cur.get(k) is not None:
                merged[k] = cur.get(k)
    return merged


def _load_reporting_rows(
    sb,
    tenant_id: str,
    revisao_id: str,
    grupo_ids: list[str],
    tarefas: list[dict],
) -> tuple[list[dict], dict[str, dict]]:
    """Monta a grade do relatório com a mesma ideia do dashboard.

    - usa grupo_servicos para construir o escopo esperado (equipamento × serviço)
    - mantém fallback por tarefas diretas quando grupo_servicos estiver incompleto
    - inclui equipamentos inativos/grupos inativos se houver tarefas apontadas
    """
    grupo_ids = [str(g) for g in (grupo_ids or []) if g]
    if not grupo_ids:
        return [], {}

    # Grupos: sem filtro ativo para não perder vínculos antigos/legados.
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
        context=f"Erro ao carregar grupos do relatório {grupo_ids}",
    )
    grupo_map = {str(r.get("id")): r for r in grupo_rows if r.get("id") is not None}

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
        context=f"Erro ao carregar grupo_servicos {grupo_ids}",
    )

    servico_ids = sorted({str(r.get("servico_id")) for r in grupo_servicos_rows if r.get("servico_id") is not None})
    serv_rows = []
    if servico_ids:
        for i in range(0, len(servico_ids), 100):
            batch = servico_ids[i:i+100]
            serv_rows.extend(_with_fallback(
                lambda batch=batch: (
                    sb.table("servicos")
                    .select("id,nome,setor")
                    .eq("tenant_id", tenant_id)
                    .in_("id", batch)
                    .execute()
                    .data
                ) or [],
                [],
                context=f"Erro ao carregar serviços {batch[:3]}...",
            ))
    serv_map = {str(r.get("id")): r for r in serv_rows if r.get("id") is not None}

    eq_rows = []
    try:
        rpc_result = sb.rpc("get_equipamentos_dashboard", {"p_tenant_id": tenant_id}).execute()
        eq_rows = [r for r in (rpc_result.data or []) if str(r.get("grupo_id") or "") in set(grupo_ids)]
    except Exception:
        eq_rows = []

    if not eq_rows:
        eq_rows = _with_fallback(
            lambda: (
                sb.table("equipamentos")
                .select("id,frota,modelo,grupo_id")
                .eq("tenant_id", tenant_id)
                .in_("grupo_id", grupo_ids)
                .execute()
                .data
            ) or [],
            [],
            context=f"Erro ao carregar equipamentos do relatório {grupo_ids}",
        )

    eq_map: dict[str, dict] = {str(r.get("id")): dict(r) for r in eq_rows if r.get("id") is not None}

    # Inclui equipamentos referenciados nas tarefas mesmo se estiverem inativos
    # ou fora do retorno da RPC.
    task_eq_ids = sorted({str(t.get("equipamento_id")) for t in tarefas if t.get("equipamento_id") is not None})
    missing_eq_ids = [eid for eid in task_eq_ids if eid not in eq_map]
    for i in range(0, len(missing_eq_ids), 100):
        batch = missing_eq_ids[i:i+100]
        if not batch:
            continue
        chunk = _with_fallback(
            lambda batch=batch: (
                sb.table("equipamentos")
                .select("id,frota,modelo,grupo_id")
                .eq("tenant_id", tenant_id)
                .in_("id", batch)
                .execute()
                .data
            ) or [],
            [],
            context=f"Erro ao carregar equipamentos por tarefa {batch[:3]}...",
        )
        for r in chunk:
            if r.get("id") is not None:
                eq_map[str(r.get("id"))] = dict(r)

    # Filtra tarefas apenas para equipamentos pertencentes aos grupos do depto.
    allowed_eq_ids = {
        eid for eid, eq in eq_map.items()
        if str(eq.get("grupo_id") or "") in set(grupo_ids)
    }

    task_map: dict[tuple[str, str], dict] = {}
    raw_tasks: list[dict] = []
    for t in tarefas:
        eid = str(t.get("equipamento_id") or "")
        if not eid or eid not in allowed_eq_ids:
            continue
        sid = str(t.get("servico_id") or "")
        eq = eq_map.get(eid, {})
        gid = str(eq.get("grupo_id") or "")
        grp = grupo_map.get(gid, {})
        svc = serv_map.get(sid, {})
        raw_tasks.append({
            "equipamento_id": eid,
            "grupo_id": gid,
            "grupo_nome": grp.get("nome") or gid,
            "departamento_id": eq.get("departamento_id") or grp.get("departamento_id"),
            "frota": eq.get("frota"),
            "modelo": eq.get("modelo"),
            "servico_id": sid or None,
            "setor_nome": svc.get("setor") or "—",
            "status": t.get("status") or "pendente",
            "etapa_d": t.get("etapa_d"),
            "etapa_r": t.get("etapa_r"),
            "etapa_m": t.get("etapa_m"),
            "updated_at": t.get("updated_at"),
            "dt_etapa_d": t.get("dt_etapa_d"),
            "dt_etapa_r": t.get("dt_etapa_r"),
            "dt_etapa_m": t.get("dt_etapa_m"),
            "semana": t.get("semana"),
        })
        if sid:
            task_map[(eid, sid)] = _merge_task_rows(task_map.get((eid, sid)), t)

    group_services: dict[str, list[str]] = {}
    for row in grupo_servicos_rows:
        gid = str(row.get("grupo_id") or "")
        sid = str(row.get("servico_id") or "")
        if gid and sid:
            group_services.setdefault(gid, [])
            if sid not in group_services[gid]:
                group_services[gid].append(sid)

    rows: list[dict] = []
    eids_covered: set[str] = set()
    for eid, eq in eq_map.items():
        gid = str(eq.get("grupo_id") or "")
        if gid not in set(grupo_ids):
            continue
        grp = grupo_map.get(gid, {})
        service_ids = group_services.get(gid, [])
        if not service_ids:
            continue
        eids_covered.add(eid)
        for sid in service_ids:
            svc = serv_map.get(str(sid), {})
            t = task_map.get((eid, str(sid)), {})
            rows.append({
                "equipamento_id": eid,
                "grupo_id": gid,
                "grupo_nome": grp.get("nome") or gid,
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
                "dt_etapa_d": t.get("dt_etapa_d"),
                "dt_etapa_r": t.get("dt_etapa_r"),
                "dt_etapa_m": t.get("dt_etapa_m"),
                "semana": t.get("semana"),
            })

    fallback_tasks = [t for t in raw_tasks if str(t.get("equipamento_id") or "") not in eids_covered]
    if fallback_tasks:
        rows.extend(fallback_tasks)
    if not rows and raw_tasks:
        rows = raw_tasks

    # Mapa final de equipamentos no escopo do relatório.
    eq_scope = {eid: eq for eid, eq in eq_map.items() if str(eq.get("grupo_id") or "") in set(grupo_ids)}
    return rows, eq_scope


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

    semanas_total = int(revisao.get("semanas_total") or 1)
    data_inicio = revisao.get("data_inicio")
    semana_atual = _semana_atual(data_inicio, semanas_total)

    rows, eq_scope = _load_reporting_rows(sb, tenant_id, str(revisao.get("id") or ""), grupo_ids, tarefas)

    # Exclui equipamentos ocultos nesta revisão.
    try:
        from src.utils.eq_oculto import get_ocultos
        ocultos = get_ocultos(sb, tenant_id, revisao.get("id", ""))
        if ocultos:
            ocultos = {str(x) for x in ocultos}
            rows = [r for r in rows if str(r.get("equipamento_id") or "") not in ocultos]
            eq_scope = {eid: eq for eid, eq in eq_scope.items() if str(eid) not in ocultos}
    except Exception:
        pass

    grupo_nomes = {}
    for gid, eq in eq_scope.items():
        pass
    grupo_nomes = {}
    for r in rows:
        gid = str(r.get("grupo_id") or "")
        if gid and gid not in grupo_nomes:
            grupo_nomes[gid] = r.get("grupo_nome") or gid
    for eid, eq in eq_scope.items():
        gid = str(eq.get("grupo_id") or "")
        if gid and gid not in grupo_nomes:
            grupo_nomes[gid] = gid

    eq_rows: dict[str, list[dict]] = {}
    for r in rows:
        eid = str(r.get("equipamento_id") or "")
        if eid:
            eq_rows.setdefault(eid, []).append(r)

    # Garante presença de equipamentos sem nenhuma linha na grade.
    for eid in list(eq_scope):
        eq_rows.setdefault(str(eid), [])

    semana_anterior = max(semana_atual - 1, 0)

    def _row_done_steps(r: dict) -> int:
        return int(_is_done(r.get("etapa_d"))) + int(_is_done(r.get("etapa_r"))) + int(_is_done(r.get("etapa_m")))

    def _row_real_ts(r: dict) -> str | None:
        etapa_ts = _best_ts(r.get("dt_etapa_m"), r.get("dt_etapa_r"), r.get("dt_etapa_d"))
        if _row_done_steps(r) > 0:
            return etapa_ts or r.get("updated_at")
        return etapa_ts

    all_equipamentos: list[dict] = []
    criticos: list[EquipamentoCritico] = []
    n_concluidos = 0
    total_done = 0
    total_expected = 0
    done_by_grupo: dict[str, int] = {}
    expected_by_grupo: dict[str, int] = {}

    for eid, sub in eq_rows.items():
        eq = eq_scope.get(eid, {})
        gid = str(eq.get("grupo_id") or (sub[0].get("grupo_id") if sub else "") or "")
        grupo_nome = grupo_nomes.get(gid) or gid
        frota = str(eq.get("frota") or (sub[0].get("frota") if sub else eid) or eid)
        modelo = str(eq.get("modelo") or (sub[0].get("modelo") if sub else "") or "")

        expected_per_eq = int(len(sub))
        done = int(sum(_row_done_steps(r) for r in sub))
        done_ant = int(sum(_row_done_steps(r) for r in sub if int(r.get("semana") or 0) <= semana_anterior))
        pct = max(0, min(100, round(done / max(expected_per_eq * 3, 1) * 100))) if expected_per_eq > 0 else 0
        pct_anterior = max(0, min(100, round(done_ant / max(expected_per_eq * 3, 1) * 100))) if expected_per_eq > 0 else 0

        total_done += done
        total_expected += expected_per_eq * 3
        done_by_grupo[gid] = done_by_grupo.get(gid, 0) + done
        expected_by_grupo[gid] = expected_by_grupo.get(gid, 0) + expected_per_eq * 3

        if expected_per_eq > 0 and pct == 100:
            n_concluidos += 1

        any_travado = any(str(r.get("status") or "").strip().lower() == "travado" for r in sub)
        if expected_per_eq == 0:
            status_eq = "sem_template"
        elif any_travado:
            status_eq = "travado"
        elif done == 0:
            status_eq = "zero"
        elif pct == 100:
            status_eq = "concluido"
        else:
            status_eq = "em_andamento"

        ultima_mov = None
        ultima_semana = None
        for r in sub:
            mov_ts = _row_real_ts(r)
            if mov_ts and (ultima_mov is None or mov_ts > ultima_mov):
                ultima_mov = mov_ts
            sem_t = int(r.get("semana") or 0)
            if _row_done_steps(r) > 0 and sem_t > 0 and (ultima_semana is None or sem_t > ultima_semana):
                ultima_semana = sem_t

        dias_sem_manut = _dias_desde(ultima_mov)
        if done == 0 and expected_per_eq > 0:
            criticos.append(EquipamentoCritico(frota=frota, modelo=modelo, grupo=grupo_nome, pct=0, status="zero"))
        elif any_travado:
            criticos.append(EquipamentoCritico(frota=frota, modelo=modelo, grupo=grupo_nome, pct=pct, status="travado"))

        all_equipamentos.append({
            "frota": frota,
            "modelo": modelo,
            "grupo": grupo_nome,
            "grupo_id": gid,
            "pct": pct,
            "pct_anterior": pct_anterior,
            "status": status_eq,
            "ultima_mov": ultima_mov,
            "ultima_semana": ultima_semana,
            "dias_sem_manut": dias_sem_manut,
        })

    pct_geral = max(0, min(100, round(total_done / max(total_expected, 1) * 100))) if total_expected > 0 else 0
    n_equipamentos = len(eq_rows)

    grupo_pct = {
        gid: (max(0, min(100, round(done_by_grupo.get(gid, 0) / max(expected_by_grupo.get(gid, 0), 1) * 100))) if expected_by_grupo.get(gid, 0) > 0 else 0)
        for gid in set(list(done_by_grupo.keys()) + list(expected_by_grupo.keys()))
    }
    for eq in all_equipamentos:
        eq["grupo_pct"] = grupo_pct.get(eq.get("grupo_id", ""), 0)

    evolucao: list[SemanaSnapshot] = []
    semana_done_steps: dict[int, int] = {}
    for r in rows:
        sem = int(r.get("semana") or 0)
        if sem > 0:
            semana_done_steps[sem] = semana_done_steps.get(sem, 0) + _row_done_steps(r)

    expected_por_semana = round(total_expected / max(semanas_total, 1)) if total_expected > 0 else 0
    cumulative_done = 0
    cumulative_expected = 0
    for sem in range(1, semana_atual + 1):
        cumulative_done += semana_done_steps.get(sem, 0)
        cumulative_expected += expected_por_semana
        cumulative_expected = min(cumulative_expected, total_expected)
        pct_sem = max(0, min(100, round(cumulative_done / max(cumulative_expected, 1) * 100))) if cumulative_expected > 0 else 0
        evolucao.append(SemanaSnapshot(semana=sem, concluidos=cumulative_done, total=cumulative_expected, pct=pct_sem))

    pct_semana_atual = evolucao[-1].pct if evolucao else pct_geral
    pct_semana_anterior = evolucao[-2].pct if len(evolucao) >= 2 else 0

    n_travados = 0
    n_sem_inicio = 0
    n_parados = 0
    n_risco_prazo = 0
    esperado_pct = _pct(semana_atual, semanas_total)
    parados_detalhe: list[dict] = []

    for eid, sub in eq_rows.items():
        eq = eq_scope.get(eid, {})
        gid = str(eq.get("grupo_id") or (sub[0].get("grupo_id") if sub else "") or "")
        grupo_nome = grupo_nomes.get(gid) or gid
        frota = str(eq.get("frota") or (sub[0].get("frota") if sub else eid) or eid)
        modelo = str(eq.get("modelo") or (sub[0].get("modelo") if sub else "") or "")
        expected_per_eq = int(len(sub))
        done = int(sum(_row_done_steps(r) for r in sub))
        pct = max(0, min(100, round(done / max(expected_per_eq * 3, 1) * 100))) if expected_per_eq > 0 else 0

        travado_eq = any(str(r.get("status") or "").strip().lower() == "travado" and ((_dias_desde(_row_real_ts(r)) or 0) >= dias_travado or _dias_desde(_row_real_ts(r)) is None) for r in sub)
        sem_inicio_eq = expected_per_eq > 0 and done == 0

        ultima_mov_eq = None
        ultima_semana_eq = None
        for r in sub:
            updated = _row_real_ts(r)
            if updated and (ultima_mov_eq is None or updated > ultima_mov_eq):
                ultima_mov_eq = updated
            sem_t = int(r.get("semana") or 0)
            if _row_done_steps(r) > 0 and sem_t > 0 and (ultima_semana_eq is None or sem_t > ultima_semana_eq):
                ultima_semana_eq = sem_t

        dias_sem_manut = _dias_desde(ultima_mov_eq)
        if dias_sem_manut is None and ultima_mov_eq is None:
            dias_sem_manut_efetivo = _dias_desde(data_inicio) if data_inicio else None
        else:
            dias_sem_manut_efetivo = dias_sem_manut

        if travado_eq:
            n_travados += 1
        if sem_inicio_eq:
            n_sem_inicio += 1

        parado_eq = (
            expected_per_eq > 0 and pct < 100 and not travado_eq and dias_sem_manut_efetivo is not None and dias_sem_manut_efetivo >= dias_sem_update
        )
        if parado_eq:
            n_parados += 1
            parados_detalhe.append({
                "frota": frota,
                "modelo": modelo,
                "grupo": grupo_nome,
                "ultima_semana": ultima_semana_eq,
                "dias_parado": dias_sem_manut_efetivo,
                "ultima_mov": ultima_mov_eq,
                "status": (
                    "Sem nenhum apontamento desde o início" if ultima_mov_eq is None else "Sem manutenção desde a semana " + (str(ultima_semana_eq) if ultima_semana_eq else "inicial")
                ),
                "progresso": pct,
            })

        if expected_per_eq > 0 and pct < esperado_pct - 15 and pct < 100:
            n_risco_prazo += 1

    parados_detalhe = sorted(parados_detalhe, key=lambda x: (-(x.get("dias_parado") or 0), str(x.get("frota") or "")))
    n_alertas_total = n_travados + n_parados + n_risco_prazo + n_sem_inicio

    return RelatorioDeptPayload(
        tenant_nome=tenant_nome or "AgroSafra",
        departamento_nome=departamento_nome,
        revisao_titulo=revisao.get("titulo") or "Revisão",
        semana_atual=semana_atual,
        semanas_total=semanas_total,
        data_inicio=data_inicio,
        pct_geral=pct_geral,
        n_equipamentos=n_equipamentos,
        n_concluidos=n_concluidos,
        n_alertas_total=n_alertas_total,
        done_steps=total_done,
        expected_steps=total_expected,
        evolucao=evolucao,
        pct_semana_anterior=pct_semana_anterior,
        pct_semana_atual=pct_semana_atual,
        criticos=sorted(criticos, key=lambda x: x.pct),
        todos_equipamentos=sorted(all_equipamentos, key=lambda e: -e["pct"]),
        n_travados=n_travados,
        n_sem_inicio=n_sem_inicio,
        n_parados=n_parados,
        n_risco_prazo=n_risco_prazo,
        parados_detalhe=parados_detalhe,
        primary_color=branding.get("primary_color") or "#FFD100",
        logo_url=branding.get("logo_url"),
    ), sorted(all_equipamentos, key=lambda e: -e["pct"])


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

    # Dados base
    revisao = _load_revisao(sb, revisao_id)
    tenant_nome = _load_tenant_nome(sb, tenant_id)
    branding = _load_branding(sb, tenant_id)

    if not revisao:
        result.errors.append("Revisão não encontrada.")
        return result

    # Grupos de destinatários (gestores — para envio de PDF por departamento)
    groups = get_recipient_groups(tenant_id)
    if dept_ids_filter:
        groups = [g for g in groups if g.departamento_id in dept_ids_filter]

    # Todos os departamentos ativos — para o relatório executivo (independente
    # de ter gestor)
    from src.services.email.recipients import _build_all_dept_groups
    all_dept_groups = _build_all_dept_groups(tenant_id)
    if dept_ids_filter:
        all_dept_groups = [
            g for g in all_dept_groups if g.departamento_id in dept_ids_filter]

    if not groups and not all_dept_groups:
        result.skipped += 1
        result.errors.append(
            "Nenhum departamento ou grupo ativo encontrado para gerar os relatórios."
        )
        return result

    _log(f"Iniciando disparo para {len(groups)} departamento(s)…")

    # Pré-carrega todas as tarefas uma única vez — evita N queries idênticas
    # (uma por departamento) que antes buscavam o mesmo conjunto do banco.
    tarefas_index = _load_tarefas_all(sb, tenant_id, revisao_id)

    for grp in groups:
        _log(f"  → Processando departamento: {grp.departamento_nome}")
        try:
            tarefas = _load_tarefas(
                sb, tenant_id, revisao_id, grp.grupo_ids,
                _tarefas_index=tarefas_index,
            )
            # Não pula departamentos sem tarefas — podem ter equipamentos com
            # 0% ainda sem início

            payload, eq_list = _build_payload(
                tarefas=tarefas,
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
            pdf_bytes = build_weekly_pdf(payload)
            pdf_name = (
                f"relatorio_{grp.departamento_nome.lower().replace(' ', '_')}"
                f"_semana{payload.semana_atual}.pdf"
            )

            # Valida integridade do PDF antes de tentar enviar.
            try:
                from src.services.reporting.pdf_validator import (
                    validate_pdf as _validate_pdf,
                    PdfValidationError as _PdfValidationError,
                )
                _pdf_validator_ok = True
            except ImportError:
                _validate_pdf = None
                _PdfValidationError = Exception
                _pdf_validator_ok = False

            try:
                if _validate_pdf is not None:
                    _validate_pdf(
                        pdf_bytes, context=f"relatorio_semanal.{grp.departamento_nome[:30]}")
            except _PdfValidationError as pdf_err:
                result.failed += 1
                msg = f"PDF inválido para {grp.departamento_nome}: {pdf_err}"
                result.errors.append(msg)
                _log(f"  ❌ {msg}")
                continue

            for rec in grp.recipients:
                result.total_emails += 1
                _log(f"    ↳ Enviando para {rec.email} ({rec.nome})")
                if dry_run:
                    _log("    ↳ [DRY RUN] — e-mail não enviado.")
                    result.sent += 1
                    continue
                try:
                    html = build_html_body(
                        destinatario_nome=rec.nome,
                        departamento_nome=grp.departamento_nome,
                        revisao_titulo=payload.revisao_titulo,
                        semana_atual=payload.semana_atual,
                        semanas_total=payload.semanas_total,
                        pct_geral=payload.pct_geral,
                        n_alertas=payload.n_alertas_total,
                        primary_color=payload.primary_color,
                        equipamentos=eq_list,
                    )
                    send_email_with_retry(EmailMessage(
                        to=[rec.email],
                        subject=(f"[{payload.revisao_titulo}] Relatório Semanal — "
                                 f"{grp.departamento_nome} · Semana {payload.semana_atual}"),
                        html_body=html,
                        pdf_bytes=pdf_bytes,
                        pdf_filename=pdf_name,
                    ), cfg=smtp_cfg,
                        on_retry=lambda attempt, exc: _log(f"    ↳ ⚠️ Retry {attempt}: {exc}"))
                    result.sent += 1
                    _log("    ↳ ✅ Enviado.")
                except Exception as e:
                    result.failed += 1
                    msg = f"Falha ao enviar para {rec.email}: {e}"
                    result.errors.append(msg)
                    _log(f"    ↳ ❌ {msg}")
                    # Enfileira na dead-letter para reprocessamento manual
                    try:
                        from src.services.email.dead_letter import enqueue_failed
                        enqueue_failed(
                            tenant_id=tenant_id,
                            revisao_id=revisao_id,
                            recipient=rec.email,
                            subject=(f"[{payload.revisao_titulo}] Relatório Semanal — "
                                     f"{grp.departamento_nome} · Semana {payload.semana_atual}"),
                            html_body=html,
                            pdf_bytes=pdf_bytes,
                            pdf_filename=pdf_name,
                            error=str(e),
                        )
                    except Exception:
                        pass  # ignorado — operação opcional

        except Exception as e:
            result.failed += 1
            msg = f"Erro no departamento {grp.departamento_nome}: {e}"
            result.errors.append(msg)
            _log(f"  ❌ {msg}")

    # ── Relatório executivo para supervisores/admins ────────────────────────
    _log("  → Gerando relatório executivo para supervisores…")
    try:
        from src.services.email.recipients import get_executive_recipients
        from src.services.reporting.pdf_relatorio_executivo import (
            build_executive_pdf, RelatorioExecutivoPayload, DeptSnapshot,
        )

        exec_recs = get_executive_recipients(tenant_id)
        if exec_recs:
            # Constrói DeptSnapshot para cada grupo de departamento já
            # processado
            dept_snapshots: list[DeptSnapshot] = []
            sem_atual_rev = _semana_atual(
                revisao.get("data_inicio"), int(
                    revisao.get("semanas_total") or 1))
            trend_acc: dict[int, dict[str, int]] = {}
            heatmap_semanal: list[dict] = []
            alertas_parados = {"atencao": 0, "critico": 0, "urgente": 0}

            for grp in all_dept_groups:  # TODOS os deptos, não só os com gestores
                try:
                    tarefas_g = _load_tarefas(
                        sb, tenant_id, revisao_id, grp.grupo_ids,
                        _tarefas_index=tarefas_index,
                    )
                    p, eq_list_g = _build_payload(
                        tarefas=tarefas_g, revisao=revisao,
                        departamento_nome=grp.departamento_nome,
                        tenant_nome=tenant_nome, branding=branding,
                        sb=sb, tenant_id=tenant_id, grupo_ids=grp.grupo_ids,
                        dias_travado=dias_travado, dias_sem_update=dias_sem_update,
                    )
                    todos = p.todos_equipamentos or []
                    top_criticos = sorted(
                        [e for e in todos if e.get("pct", 0) < 100],
                        key=lambda e: e.get("pct", 0)
                    )[:3]
                    top_melhores = sorted(
                        [e for e in todos if e.get("pct", 0) < 100],
                        key=lambda e: -e.get("pct", 0)
                    )[:3]
                    maiores_evolucoes = sorted(
                        [e for e in todos if e.get("pct", 0) - int(e.get("pct_anterior", 0)) > 0],
                        key=lambda e: -(e.get("pct", 0) - int(e.get("pct_anterior", 0)))
                    )[:3]
                    dept_snapshots.append(DeptSnapshot(
                        nome=grp.departamento_nome,
                        pct_geral=p.pct_geral,
                        pct_anterior=p.pct_semana_anterior,
                        n_equipamentos=p.n_equipamentos,
                        n_concluidos=p.n_concluidos,
                        n_travados=p.n_travados,
                        n_sem_inicio=p.n_sem_inicio,
                        n_risco_prazo=p.n_risco_prazo,
                        top_criticos=top_criticos,
                        top_melhores=top_melhores,
                        maiores_evolucoes=maiores_evolucoes,
                        n_parados=p.n_parados,
                        max_dias_parado=max([int(x.get("dias_parado") or 0) for x in (p.parados_detalhe or [])] or [0]),
                        _done_steps=p.done_steps,
                        _expected_steps=p.expected_steps,
                    ))

                    for wk in (p.evolucao or []):
                        sem = int(getattr(wk, "semana", 0) or 0)
                        if sem <= 0:
                            continue
                        acc = trend_acc.setdefault(
                            sem, {"done": 0, "total": 0})
                        acc["done"] += int(getattr(wk, "concluidos", 0) or 0)
                        acc["total"] += int(getattr(wk, "total", 0) or 0)
                        heatmap_semanal.append({
                            "departamento": grp.departamento_nome,
                            "semana": sem,
                            "pct": int(getattr(wk, "pct", 0) or 0),
                        })

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
                # pct_global ponderado: sum(done_steps) / sum(expected_steps)
                # idêntico à fórmula do kpi_engine — evita distorção por deptos
                # de tamanhos diferentes
                total_done_g = sum(getattr(s, "_done_steps", 0)
                                   for s in dept_snapshots)
                total_expected_g = sum(
                    getattr(
                        s,
                        "_expected_steps",
                        0) for s in dept_snapshots)
                pct_global = (
                    max(0, min(100, round(total_done_g / total_expected_g * 100)))
                    if total_expected_g > 0
                    else round(sum(d.pct_geral for d in dept_snapshots) / max(len(dept_snapshots), 1))
                )
                n_equip_total = sum(d.n_equipamentos for d in dept_snapshots)
                n_equip_concl = sum(d.n_concluidos for d in dept_snapshots)
                n_alertas_total = sum(
                    d.n_travados +
                    d.n_risco_prazo +
                    d.n_parados +
                    d.n_sem_inicio for d in dept_snapshots)

                trend_semanal = []
                for sem in sorted(trend_acc):
                    total_sem = int(trend_acc[sem].get("total") or 0)
                    done_sem = int(trend_acc[sem].get("done") or 0)
                    pct_sem = max(
                        0, min(
                            100, round(
                                done_sem / total_sem * 100))) if total_sem > 0 else 0
                    trend_semanal.append({"semana": sem, "pct": pct_sem})
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
                )
                pdf_exec = build_executive_pdf(exec_payload)
                pdf_name_e = f"relatorio_executivo_semana{sem_atual_rev}.pdf"

                for rec in exec_recs:
                    result.total_emails += 1
                    _log(f"    ↳ Executivo → {rec.email} ({rec.nome})")
                    if dry_run:
                        _log("    ↳ [DRY RUN] — e-mail não enviado.")
                        result.sent += 1
                        continue
                    try:
                        from src.services.email.smtp_sender import build_html_body, EmailMessage, send_email_with_retry
                        html_e = build_html_body(
                            destinatario_nome=rec.nome,
                            departamento_nome="Visão geral — todos os departamentos",
                            revisao_titulo=exec_payload.revisao_titulo,
                            semana_atual=exec_payload.semana_atual,
                            semanas_total=exec_payload.semanas_total,
                            pct_geral=exec_payload.pct_global,
                            n_alertas=exec_payload.n_alertas_total,
                            primary_color=exec_payload.primary_color,
                        )
                        send_email_with_retry(EmailMessage(
                            to=[rec.email],
                            subject=(f"[{exec_payload.revisao_titulo}] Visão Executiva — "
                                     f"Semana {exec_payload.semana_atual}/{exec_payload.semanas_total}"),
                            html_body=html_e,
                            pdf_bytes=pdf_exec,
                            pdf_filename=pdf_name_e,
                        ), cfg=smtp_cfg,
                            on_retry=lambda attempt, exc: _log(f"    ↳ ⚠️ Retry {attempt}: {exc}"))
                        result.sent += 1
                        _log("    ↳ ✅ Executivo enviado.")
                    except Exception as e_send:
                        result.failed += 1
                        msg = f"Falha ao enviar executivo para {rec.email}: {e_send}"
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
