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

import pandas as pd

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



def _build_equipment_snapshot_direct(
    *,
    sb,
    tenant_id: str,
    grupo_ids: list[str],
    tarefas: list[dict],
    semana_atual: int,
    data_inicio: str | None,
):
    """Calcula progresso por frota diretamente das tarefas reais + template do grupo.

    Evita depender da base normalizada do dashboard para os PDFs de e-mail,
    onde o objetivo principal é manter os percentuais por frota alinhados com
    as tarefas efetivamente apontadas na revisão.
    """
    grupo_ids = [str(g) for g in (grupo_ids or []) if g]
    if not grupo_ids:
        return {
            'all_equipamentos': [], 'overall_pct': 0, 'n_equipamentos': 0,
            'n_concluidos': 0, 'done_steps_total': 0, 'expected_steps_total': 0,
            'n_travados': 0, 'n_sem_inicio': 0, 'n_parados': 0, 'n_risco_prazo': 0,
            'parados_detalhe': [], 'criticos': [], 'evolucao': [],
            'pct_semana_anterior': 0, 'pct_semana_atual': 0,
        }

    eq_por_grupo = _load_equipamentos_ativos(sb, tenant_id, grupo_ids)
    grupo_rows = _with_fallback(
        lambda: (
            sb.table('equip_grupos')
            .select('id,nome,departamento_id')
            .eq('tenant_id', tenant_id)
            .in_('id', grupo_ids)
            .execute().data
        ) or [], [], context=f'Erro ao carregar grupos {grupo_ids}'
    )
    grupo_map = {str(r.get('id')): r for r in grupo_rows if r.get('id') is not None}

    grupo_servicos_rows = _with_fallback(
        lambda: (
            sb.table('grupo_servicos')
            .select('grupo_id,servico_id')
            .eq('tenant_id', tenant_id)
            .in_('grupo_id', grupo_ids)
            .execute().data
        ) or [], [], context=f'Erro ao carregar grupo_servicos dos grupos {grupo_ids}'
    )
    group_services: dict[str, list[str]] = {}
    for row in grupo_servicos_rows:
        gid = row.get('grupo_id')
        sid = row.get('servico_id')
        if gid is None or sid is None:
            continue
        gid_s, sid_s = str(gid), str(sid)
        group_services.setdefault(gid_s, [])
        if sid_s not in group_services[gid_s]:
            group_services[gid_s].append(sid_s)

    eq_rows: list[dict] = []
    eq_gid_map: dict[str, str] = {}
    for gid, eqs in (eq_por_grupo or {}).items():
        for eq in eqs or []:
            row = dict(eq)
            row['grupo_id'] = str(gid)
            eq_rows.append(row)
            if row.get('id') is not None:
                eq_gid_map[str(row.get('id'))] = str(gid)

    def _status_rank(status: str | None) -> int:
        s = str(status or '').strip().lower()
        order = {
            'concluido': 4, 'concluído': 4,
            'em_andamento': 3, 'em andamento': 3, 'andamento': 3,
            'travado': 2, 'pendente': 1,
            'nao_aplica': 0, 'não aplica': 0, 'nao aplica': 0,
        }
        return order.get(s, -1)

    def _merge_task(prev: dict | None, cur: dict) -> dict:
        if not prev:
            return dict(cur)
        merged = dict(prev)
        for etapa_col in ('etapa_d', 'etapa_r', 'etapa_m'):
            merged[etapa_col] = _is_done(prev.get(etapa_col)) or _is_done(cur.get(etapa_col))
        prev_status = prev.get('status')
        cur_status = cur.get('status')
        merged['status'] = cur_status if _status_rank(cur_status) >= _status_rank(prev_status) else prev_status
        prev_upd = str(prev.get('updated_at') or '')
        cur_upd = str(cur.get('updated_at') or '')
        merged['updated_at'] = cur.get('updated_at') if cur_upd >= prev_upd else prev.get('updated_at')
        return merged

    task_map: dict[tuple[str, str], dict] = {}
    eq_tasks: dict[str, list[dict]] = {}
    for t in tarefas or []:
        eid = str(t.get('equipamento_id') or '')
        sid = str(t.get('servico_id') or '')
        gid = eq_gid_map.get(eid)
        if not eid or not sid or not gid:
            continue
        if sid not in set(group_services.get(gid, [])):
            continue
        eq_tasks.setdefault(eid, []).append(t)
        task_map[(eid, sid)] = _merge_task(task_map.get((eid, sid)), t)

    all_equipamentos: list[dict] = []
    criticos = []
    parados_detalhe: list[dict] = []
    n_concluidos = n_travados = n_sem_inicio = n_parados = n_risco_prazo = 0
    done_steps_total = expected_steps_total = 0
    esperado_pct = _pct(semana_atual, max(semana_atual, 1)) if semana_atual <= 1 else _pct(semana_atual, semana_atual)
    # meta linear aproximada usando a própria semana atual como denominador em revisões ainda no início
    esperado_pct = max(0, min(100, esperado_pct))

    for eq in eq_rows:
        eid = str(eq.get('id') or '')
        gid = str(eq.get('grupo_id') or '')
        grupo_nome = str(grupo_map.get(gid, {}).get('nome') or gid or '—')
        frota = str(eq.get('frota') or '—')
        modelo = str(eq.get('modelo') or '')
        service_ids = group_services.get(gid, [])
        expected_steps = len(service_ids) * 3
        done_steps = 0
        travados_count = 0
        concluidos_count = 0
        em_andamento_count = 0
        pendentes_count = 0
        for sid in service_ids:
            t = task_map.get((eid, sid))
            if t:
                done_steps += _sum_done_steps(t)
                st = str(t.get('status') or '').strip().lower()
                if st == 'travado':
                    travados_count += 1
                elif st in {'concluido', 'concluído'}:
                    concluidos_count += 1
                elif _sum_done_steps(t) > 0:
                    em_andamento_count += 1
                else:
                    pendentes_count += 1
            else:
                pendentes_count += 1
        pct = max(0, min(100, round(done_steps / max(expected_steps, 1) * 100))) if expected_steps > 0 else 0
        done_steps_total += done_steps
        expected_steps_total += expected_steps

        tasks = eq_tasks.get(eid, [])
        done_ant = sum(_sum_done_steps(t) for t in tasks if int(t.get('semana') or 0) <= max(semana_atual - 1, 0))
        pct_anterior = max(0, min(100, round(done_ant / max(expected_steps, 1) * 100))) if expected_steps > 0 else 0

        if pct == 100 and expected_steps > 0:
            n_concluidos += 1
        any_travado = travados_count > 0
        if expected_steps == 0:
            status_eq = 'sem_template'
        elif any_travado:
            status_eq = 'travado'
        elif done_steps == 0:
            status_eq = 'zero'
        elif pct == 100:
            status_eq = 'concluido'
        else:
            status_eq = 'em_andamento'

        ultima_mov = None
        ultima_semana = None
        for t in tasks:
            mov_ts = _best_ts(t.get('dt_etapa_m'), t.get('dt_etapa_r'), t.get('dt_etapa_d'), t.get('updated_at'))
            if mov_ts and (ultima_mov is None or mov_ts > ultima_mov):
                ultima_mov = mov_ts
            sem_t = int(t.get('semana') or 0)
            if _sum_done_steps(t) > 0 and sem_t > 0 and (ultima_semana is None or sem_t > ultima_semana):
                ultima_semana = sem_t

        dias_sem_manut = _dias_desde(ultima_mov)
        dias_sem_manut_efetivo = _dias_desde(data_inicio) if (dias_sem_manut is None and ultima_mov is None and data_inicio) else dias_sem_manut

        if done_steps == 0 and expected_steps > 0:
            criticos.append({'frota': frota, 'modelo': modelo, 'grupo': grupo_nome, 'pct': 0, 'status': 'zero'})
        elif any_travado:
            criticos.append({'frota': frota, 'modelo': modelo, 'grupo': grupo_nome, 'pct': pct, 'status': 'travado'})

        if any_travado:
            n_travados += 1
        if done_steps == 0 and expected_steps > 0:
            n_sem_inicio += 1

        parado_eq = (
            expected_steps > 0 and pct < 100 and not any_travado and
            dias_sem_manut_efetivo is not None and dias_sem_manut_efetivo >= 5
        )
        if parado_eq:
            n_parados += 1
            parados_detalhe.append({
                'frota': frota, 'modelo': modelo, 'grupo': grupo_nome,
                'ultima_semana': ultima_semana, 'dias_parado': dias_sem_manut_efetivo,
                'ultima_mov': ultima_mov,
                'status': 'Sem nenhum apontamento desde o início' if ultima_mov is None else 'Sem manutenção desde a semana ' + (str(ultima_semana) if ultima_semana else 'inicial'),
                'progresso': pct,
            })
        if expected_steps > 0 and pct < max(esperado_pct - 15, 0) and pct < 100:
            n_risco_prazo += 1

        all_equipamentos.append({
            'equipamento_id': eid,
            'frota': frota,
            'modelo': modelo,
            'grupo': grupo_nome,
            'grupo_id': gid,
            'pct': pct,
            'pct_anterior': pct_anterior,
            'status': status_eq,
            'done_steps': done_steps,
            'total_steps': expected_steps,
            'ultima_mov': ultima_mov,
            'ultima_semana': ultima_semana,
            'dias_sem_manut': dias_sem_manut_efetivo,
            'grupo_pct': pct,
        })

    overall_pct = max(0, min(100, round(done_steps_total / max(expected_steps_total, 1) * 100))) if expected_steps_total > 0 else 0

    # Evolução: se não houver semanas fechadas com coluna semana preenchida, mantém o snapshot atual.
    semana_done_steps: dict[int, int] = {}
    for t in tarefas or []:
        eid = str(t.get('equipamento_id') or '')
        sid = str(t.get('servico_id') or '')
        gid = eq_gid_map.get(eid)
        sem = int(t.get('semana') or 0)
        if not gid or sem <= 0 or sid not in set(group_services.get(gid, [])):
            continue
        semana_done_steps[sem] = semana_done_steps.get(sem, 0) + _sum_done_steps(t)

    evolucao = []
    pct_semana_anterior = 0
    pct_semana_atual = overall_pct
    if semana_done_steps and expected_steps_total > 0:
        cumulative_done = 0
        expected_por_semana = round(expected_steps_total / max(semana_atual, 1))
        cumulative_expected = 0
        for sem in range(1, semana_atual + 1):
            cumulative_done += semana_done_steps.get(sem, 0)
            cumulative_expected = min(expected_steps_total, cumulative_expected + expected_por_semana)
            pct_sem = max(0, min(100, round(cumulative_done / max(cumulative_expected, 1) * 100)))
            evolucao.append({'semana': sem, 'concluidos': cumulative_done, 'total': cumulative_expected, 'pct': pct_sem})
        pct_semana_atual = evolucao[-1]['pct'] if evolucao else overall_pct
        pct_semana_anterior = evolucao[-2]['pct'] if len(evolucao) >= 2 else 0
    else:
        evolucao = [{'semana': max(int(semana_atual or 1), 1), 'concluidos': done_steps_total, 'total': expected_steps_total, 'pct': overall_pct}] if expected_steps_total > 0 else []

    parados_detalhe = sorted(parados_detalhe, key=lambda x: (-(x.get('dias_parado') or 0), str(x.get('frota') or '')))
    criticos_sorted = sorted(criticos, key=lambda x: (int(x.get('pct', 0) or 0), str(x.get('frota') or '')))
    all_equipamentos = sorted(all_equipamentos, key=lambda e: (-int(e.get('pct', 0) or 0), str(e.get('frota') or '')))

    return {
        'all_equipamentos': all_equipamentos,
        'overall_pct': overall_pct,
        'n_equipamentos': len(all_equipamentos),
        'n_concluidos': n_concluidos,
        'done_steps_total': done_steps_total,
        'expected_steps_total': expected_steps_total,
        'n_travados': n_travados,
        'n_sem_inicio': n_sem_inicio,
        'n_parados': n_parados,
        'n_risco_prazo': n_risco_prazo,
        'parados_detalhe': parados_detalhe,
        'criticos': criticos_sorted,
        'evolucao': evolucao,
        'pct_semana_anterior': pct_semana_anterior,
        'pct_semana_atual': pct_semana_atual,
    }


def _dashboard_equipment_fallback(
    *,
    sb,
    tenant_id: str,
    revisao_id: str,
    grupo_ids: list[str],
    tarefas: list[dict] | None = None,
):
    """Lê o mesmo detalhamento por frota do dashboard e devolve uma lista compatível.

    Usado como fallback quando o cálculo direto do e-mail zera alguma frota que
    no dashboard aparece com progresso.
    """
    try:
        from src.ui.pages.dashboard.transforms import equipment_progress
        base, _ = _build_dashboard_base(sb, tenant_id, revisao_id, grupo_ids, tarefas)
        if base is None or getattr(base, "empty", True):
            return []
        eq_prog = equipment_progress(base)
        if eq_prog is None or getattr(eq_prog, "empty", True):
            return []

        rows = []
        for _, row in eq_prog.iterrows():
            pct = int(round(float(row.get("% Concluído") or 0)))
            rows.append({
                "equipamento_id": str(row.get("equipamento_id") or ""),
                "frota": str(row.get("Frota") or "—"),
                "modelo": str(row.get("Modelo") or ""),
                "grupo": str(row.get("grupo") or "—"),
                "grupo_id": str(row.get("grupo_id") or ""),
                "pct": pct,
                "pct_anterior": 0,
                "status": (
                    "sem_template" if int(row.get("expected_steps") or 0) <= 0 else
                    "travado" if int(row.get("Travados") or 0) > 0 else
                    "zero" if int(row.get("done_steps") or 0) <= 0 else
                    "concluido" if pct >= 100 else
                    "em_andamento"
                ),
                "done_steps": int(row.get("done_steps") or 0),
                "total_steps": int(row.get("expected_steps") or 0),
                "ultima_mov": None,
                "ultima_semana": None,
                "dias_sem_manut": None,
                "grupo_pct": pct,
            })
        return rows
    except Exception:
        return []


def _merge_email_with_dashboard_equipment(email_rows: list[dict], dash_rows: list[dict]) -> list[dict]:
    """Mescla o snapshot do e-mail com o detalhamento do dashboard.

    Regra: quando a frota do e-mail vier zerada mas a do dashboard tiver
    progresso real, prevalece o dashboard.
    """
    by_key = {}
    def _key(r: dict):
        eid = str(r.get("equipamento_id") or "").strip()
        if eid:
            return ("eid", eid)
        return ("frota", str(r.get("frota") or "").strip().lower())

    for row in email_rows or []:
        by_key[_key(row)] = dict(row)

    for d in dash_rows or []:
        k = _key(d)
        cur = by_key.get(k)
        if not cur:
            by_key[k] = dict(d)
            continue

        cur_pct = int(cur.get("pct", 0) or 0)
        dash_pct = int(d.get("pct", 0) or 0)
        cur_done = int(cur.get("done_steps", 0) or 0)
        dash_done = int(d.get("done_steps", 0) or 0)
        cur_total = int(cur.get("total_steps", 0) or 0)
        dash_total = int(d.get("total_steps", 0) or 0)

        # Prefere dashboard quando ele traz avanço real e o e-mail veio zerado/inferior.
        if dash_pct > cur_pct or (dash_done > cur_done and dash_pct >= cur_pct):
            merged = dict(cur)
            merged.update({
                "frota": d.get("frota") or cur.get("frota"),
                "modelo": d.get("modelo") or cur.get("modelo"),
                "grupo": d.get("grupo") or cur.get("grupo"),
                "grupo_id": d.get("grupo_id") or cur.get("grupo_id"),
                "pct": dash_pct,
                "done_steps": max(cur_done, dash_done),
                "total_steps": max(cur_total, dash_total),
                "status": d.get("status") or cur.get("status"),
                "grupo_pct": max(int(cur.get("grupo_pct", 0) or 0), int(d.get("grupo_pct", 0) or 0), dash_pct),
            })
            by_key[k] = merged
        else:
            cur["done_steps"] = max(cur_done, dash_done)
            cur["total_steps"] = max(cur_total, dash_total)

    rows = list(by_key.values())
    rows.sort(key=lambda e: (-int(e.get("pct", 0) or 0), str(e.get("frota") or "")))
    return rows


def _recompute_snapshot_from_rows(rows: list[dict], semana_atual: int) -> dict:
    done_steps_total = sum(int(r.get("done_steps", 0) or 0) for r in rows)
    expected_steps_total = sum(int(r.get("total_steps", 0) or 0) for r in rows)
    overall_pct = max(0, min(100, round(done_steps_total / max(expected_steps_total, 1) * 100))) if expected_steps_total > 0 else 0
    n_concluidos = sum(1 for r in rows if int(r.get("pct", 0) or 0) >= 100 and int(r.get("total_steps", 0) or 0) > 0)
    n_travados = sum(1 for r in rows if str(r.get("status") or "") == "travado")
    n_sem_inicio = sum(1 for r in rows if str(r.get("status") or "") == "zero")
    return {
        "all_equipamentos": rows,
        "overall_pct": overall_pct,
        "n_equipamentos": len(rows),
        "n_concluidos": n_concluidos,
        "done_steps_total": done_steps_total,
        "expected_steps_total": expected_steps_total,
        "n_travados": n_travados,
        "n_sem_inicio": n_sem_inicio,
        "pct_semana_atual": overall_pct,
        "pct_semana_anterior": 0 if semana_atual <= 1 else None,
    }

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

    # Tarefas flat da revisão
    if tarefas is None:
        tarefas = _with_fallback(
            lambda: (
                sb.table("tarefas_servico")
                .select("equipamento_id,servico_id,status,etapa_d,etapa_r,etapa_m,updated_at")
                .eq("tenant_id", tenant_id)
                .eq("revisao_id", revisao_id)
                .execute()
                .data
            ) or [],
            [],
            context=f"Erro ao carregar tarefas base da revisão {revisao_id}",
        )

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

    semanas_total = int(revisao.get("semanas_total") or 1)
    data_inicio = revisao.get("data_inicio")
    semana_atual = _semana_atual(data_inicio, semanas_total)

    snapshot = _build_equipment_snapshot_direct(
        sb=sb,
        tenant_id=tenant_id,
        grupo_ids=grupo_ids,
        tarefas=tarefas or [],
        semana_atual=semana_atual,
        data_inicio=data_inicio,
    )

    # Fallback do dashboard: se alguma frota vier zerada no e-mail, mas o dashboard
    # tiver progresso real, mescla os dois snapshots e prevalece o dashboard.
    dash_rows = _dashboard_equipment_fallback(
        sb=sb,
        tenant_id=tenant_id,
        revisao_id=revisao.get("id", ""),
        grupo_ids=grupo_ids,
        tarefas=tarefas or [],
    )
    if dash_rows:
        merged_rows = _merge_email_with_dashboard_equipment(
            list(snapshot.get('all_equipamentos') or []),
            dash_rows,
        )
        if merged_rows:
            recomputed = _recompute_snapshot_from_rows(merged_rows, semana_atual)
            snapshot['all_equipamentos'] = recomputed['all_equipamentos']
            snapshot['overall_pct'] = max(int(snapshot.get('overall_pct') or 0), int(recomputed.get('overall_pct') or 0))
            snapshot['n_equipamentos'] = max(int(snapshot.get('n_equipamentos') or 0), int(recomputed.get('n_equipamentos') or 0))
            snapshot['n_concluidos'] = max(int(snapshot.get('n_concluidos') or 0), int(recomputed.get('n_concluidos') or 0))
            snapshot['done_steps_total'] = max(int(snapshot.get('done_steps_total') or 0), int(recomputed.get('done_steps_total') or 0))
            snapshot['expected_steps_total'] = max(int(snapshot.get('expected_steps_total') or 0), int(recomputed.get('expected_steps_total') or 0))
            snapshot['n_travados'] = max(int(snapshot.get('n_travados') or 0), int(recomputed.get('n_travados') or 0))
            snapshot['n_sem_inicio'] = min(
                int(snapshot.get('n_sem_inicio') or 0),
                sum(1 for r in merged_rows if int(r.get('pct', 0) or 0) <= 0 and int(r.get('total_steps', 0) or 0) > 0)
            )
            snapshot['pct_semana_atual'] = int(snapshot.get('overall_pct') or 0)
            if not snapshot.get('evolucao'):
                snapshot['evolucao'] = [{
                    'semana': max(int(semana_atual or 1), 1),
                    'concluidos': int(snapshot.get('done_steps_total') or 0),
                    'total': int(snapshot.get('expected_steps_total') or 0),
                    'pct': int(snapshot.get('overall_pct') or 0),
                }]

    evolucao = [
        SemanaSnapshot(
            semana=int(r.get('semana') or 0),
            concluidos=int(r.get('concluidos') or 0),
            total=int(r.get('total') or 0),
            pct=int(r.get('pct') or 0),
        )
        for r in (snapshot.get('evolucao') or [])
        if int(r.get('semana') or 0) > 0
    ]

    criticos = [
        EquipamentoCritico(
            frota=str(eq.get('frota') or '—'),
            modelo=str(eq.get('modelo') or ''),
            grupo=str(eq.get('grupo') or '—'),
            pct=int(eq.get('pct') or 0),
            status=str(eq.get('status') or 'zero'),
        )
        for eq in (snapshot.get('criticos') or [])
    ]

    all_equipamentos = list(snapshot.get('all_equipamentos') or [])
    parados_detalhe = list(snapshot.get('parados_detalhe') or [])
    n_alertas_total = int(snapshot.get('n_travados') or 0) + int(snapshot.get('n_parados') or 0) + int(snapshot.get('n_risco_prazo') or 0) + int(snapshot.get('n_sem_inicio') or 0)

    payload = RelatorioDeptPayload(
        tenant_nome=tenant_nome or 'AgroSafra',
        departamento_nome=departamento_nome,
        revisao_titulo=revisao.get('titulo') or 'Revisão',
        semana_atual=semana_atual,
        semanas_total=semanas_total,
        data_inicio=data_inicio,
        pct_geral=int(snapshot.get('overall_pct') or 0),
        n_equipamentos=int(snapshot.get('n_equipamentos') or 0),
        n_concluidos=int(snapshot.get('n_concluidos') or 0),
        n_alertas_total=n_alertas_total,
        done_steps=int(snapshot.get('done_steps_total') or 0),
        expected_steps=int(snapshot.get('expected_steps_total') or 0),
        evolucao=evolucao,
        pct_semana_anterior=int(snapshot.get('pct_semana_anterior') or 0) if int(semana_atual or 1) > 1 else 0,
        pct_semana_atual=int(snapshot.get('pct_semana_atual') or 0),
        criticos=criticos,
        todos_equipamentos=all_equipamentos,
        n_travados=int(snapshot.get('n_travados') or 0),
        n_sem_inicio=int(snapshot.get('n_sem_inicio') or 0),
        n_parados=int(snapshot.get('n_parados') or 0),
        n_risco_prazo=int(snapshot.get('n_risco_prazo') or 0),
        parados_detalhe=parados_detalhe,
        primary_color=branding.get('primary_color') or '#FFD100',
        logo_url=branding.get('logo_url'),
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

                    # Acumula trend e monta heatmap usando pct INCREMENTAL por semana.
                    # wk.pct é cumulativo (semanas 1..N); para o heatmap queremos
                    # o progresso *daquela* semana isolada, calculado pelo delta
                    # done/total entre semanas consecutivas.
                    evolucao_sorted = sorted(p.evolucao or [], key=lambda w: getattr(w, "semana", 0))
                    prev_done = 0
                    for wk in evolucao_sorted:
                        sem = int(getattr(wk, "semana", 0) or 0)
                        if sem <= 0:
                            continue
                        wk_done = int(getattr(wk, "concluidos", 0) or 0)
                        wk_total = int(getattr(wk, "total", 0) or 0)
                        acc = trend_acc.setdefault(sem, {"done": 0, "total": 0})
                        acc["done"] += wk_done
                        acc["total"] += wk_total
                        # pct incremental: etapas feitas *nessa semana* / total esperado
                        delta_done = wk_done - prev_done
                        pct_incremental = max(0, min(100, round(
                            delta_done / max(wk_total, 1) * 100
                        ))) if wk_total > 0 else int(getattr(wk, "pct", 0) or 0)
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
