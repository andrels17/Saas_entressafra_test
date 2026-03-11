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
from datetime import datetime
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)


# ── helpers internos ──────────────────────────────────────────────────────────

def _pct(done: int, total: int) -> int:
    return round((done / max(total, 1)) * 100)


def _dias_desde(ts_str: str | None) -> int | None:
    if not ts_str:
        return None
    try:
        ts = pd.to_datetime(ts_str, utc=True)
        return int((pd.Timestamp.utcnow() - ts).total_seconds() // 86400)
    except Exception:
        return None


def _semana_atual(data_inicio_str: str | None, semanas_total: int) -> int:
    if not data_inicio_str:
        return 1
    try:
        inicio = pd.to_datetime(data_inicio_str, utc=True)
        agora = pd.Timestamp.utcnow()
        semana = max(1, int((agora - inicio).days // 7) + 1)
        return min(semana, semanas_total or semana)
    except Exception:
        return 1


# ── Carregamento de dados ─────────────────────────────────────────────────────

def _load_tarefas(sb, tenant_id: str, revisao_id: str, grupo_ids: list[str]) -> list[dict]:
    """Carrega tarefas de uma lista de grupos para um tenant/revisão."""
    if not grupo_ids:
        return []
    try:
        rows = (
            sb.table("tarefas_servico")
            .select(
                "id,equipamento_id,servico_id,status,semana,"
                "etapa_d,etapa_r,etapa_m,observacao,updated_at,"
                "dt_etapa_d,dt_etapa_r,dt_etapa_m,"
                "equipamentos(id,frota,modelo,grupo_id,"
                "equip_grupos(id,nome,departamento_id))"
            )
            .eq("tenant_id", tenant_id)
            .eq("revisao_id", revisao_id)
            .execute()
            .data
        ) or []
        # filtra pelo grupo
        grupo_set = set(grupo_ids)
        return [
            t for t in rows
            if (t.get("equipamentos") or {}).get("grupo_id") in grupo_set
        ]
    except Exception as e:
        log.warning("Erro ao carregar tarefas: %s", e)
        return []


def _load_grupo_template(sb, tenant_id: str, grupo_ids: list[str]) -> dict[str, int]:
    """Retorna {grupo_id: svc_count} — número de serviços do template por grupo.
    Mesma fonte usada pela Matriz para calcular o denominador correto.
    """
    if not grupo_ids:
        return {}
    try:
        rows = (
            sb.table("grupo_servicos")
            .select("grupo_id,servico_id")
            .eq("tenant_id", tenant_id)
            .in_("grupo_id", grupo_ids)
            .execute()
            .data
        ) or []
        svc_map: dict[str, set] = {}
        for r in rows:
            gid = r.get("grupo_id")
            sid = r.get("servico_id")
            if gid and sid:
                svc_map.setdefault(gid, set()).add(sid)
        return {gid: len(svcs) for gid, svcs in svc_map.items()}
    except Exception:
        return {}


def _load_equipamentos_ativos(sb, tenant_id: str, grupo_ids: list[str]) -> dict[str, list[dict]]:
    """Retorna {grupo_id: [{id, frota, modelo}]} — equipamentos ativos por grupo."""
    if not grupo_ids:
        return {}
    try:
        rows = (
            sb.table("equipamentos")
            .select("id,frota,modelo,grupo_id")
            .eq("tenant_id", tenant_id)
            .eq("ativo", True)
            .in_("grupo_id", grupo_ids)
            .execute()
            .data
        ) or []
        out: dict[str, list] = {}
        for r in rows:
            gid = r.get("grupo_id")
            if gid:
                out.setdefault(gid, []).append(r)
        return out
    except Exception:
        return {}


def _load_revisao(sb, revisao_id: str) -> dict:
    try:
        rows = (
            sb.table("revisoes")
            .select("id,titulo,data_inicio,semanas_total,status")
            .eq("id", revisao_id)
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else {}
    except Exception:
        return {}


def _load_tenant_nome(sb, tenant_id: str) -> str:
    try:
        rows = (
            sb.table("tenants")
            .select("nome")
            .eq("id", tenant_id)
            .limit(1)
            .execute()
            .data
        )
        return (rows[0].get("nome") or "") if rows else ""
    except Exception:
        return ""


def _load_branding(sb, tenant_id: str) -> dict:
    try:
        rows = (
            sb.table("tenant_branding")
            .select("primary_color,logo_url")
            .eq("tenant_id", tenant_id)
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else {}
    except Exception:
        return {}


# ── Construção do payload ─────────────────────────────────────────────────────

def _build_payload(
    *,
    tarefas: list[dict],
    revisao: dict,
    departamento_nome: str,
    tenant_nome: str,
    branding: dict,
    sb,                         # conexão supabase para consultar template
    tenant_id: str,
    grupo_ids: list[str],
    dias_travado: int = 2,
    dias_sem_update: int = 5,
):
    from src.services.reporting.pdf_relatorio_semanal import (
        RelatorioDeptPayload, SemanaSnapshot, EquipamentoCritico,
    )

    semanas_total = int(revisao.get("semanas_total") or 1)
    data_inicio   = revisao.get("data_inicio")
    semana_atual  = _semana_atual(data_inicio, semanas_total)

    # ── Fonte de verdade: mesma fórmula da Matriz ──────────────────────────────
    # denominador = eq_count_ativo × svc_count_template × 3
    svc_por_grupo   = _load_grupo_template(sb, tenant_id, grupo_ids)
    eq_por_grupo    = _load_equipamentos_ativos(sb, tenant_id, grupo_ids)

    # Mapa eid → info do equipamento (frota, modelo, grupo_nome, grupo_id)
    eid_to_info: dict[str, dict] = {}
    for gid, eqs in eq_por_grupo.items():
        for eq in eqs:
            eid_to_info[eq["id"]] = {
                "frota":  str(eq.get("frota") or eq["id"]),
                "modelo": str(eq.get("modelo") or ""),
                "grupo_id": gid,
            }

    # Agrupa tarefas por equipamento_id
    eq_tasks: dict[str, list] = {}
    for t in tarefas:
        eid = t.get("equipamento_id") or (t.get("equipamentos") or {}).get("id", "")
        if eid:
            eq_tasks.setdefault(eid, []).append(t)

    # Acumula done_steps por grupo (para pct_geral com denominador correto)
    done_by_grupo: dict[str, int] = {}
    for gid in grupo_ids:
        done_by_grupo[gid] = 0

    # Progresso por equipamento (denominador = svc_count × 3)
    criticos: list[EquipamentoCritico] = []
    all_equipamentos: list[dict] = []
    n_concluidos = 0

    for gid, eqs in eq_por_grupo.items():
        svc_count = svc_por_grupo.get(gid, 0)
        expected_per_eq = svc_count * 3  # denominador correto por equipamento

        # nome do grupo a partir das tarefas (fallback)
        grupo_nome = "—"
        for eq in eqs:
            eid = eq["id"]
            tasks = eq_tasks.get(eid, [])
            if tasks:
                grupo_nome = str((tasks[0].get("equipamentos") or {}).get("equip_grupos", {}).get("nome") or grupo_nome)
                break

        for eq in eqs:
            eid = eq["id"]
            frota  = str(eq.get("frota") or eid)
            modelo = str(eq.get("modelo") or "")
            tasks  = eq_tasks.get(eid, [])

            done = sum(
                int(bool(t.get("etapa_d"))) + int(bool(t.get("etapa_r"))) + int(bool(t.get("etapa_m")))
                for t in tasks
            )
            done_by_grupo[gid] = done_by_grupo.get(gid, 0) + done

            # pct usando denominador correto (eq × svc × 3)
            if expected_per_eq > 0:
                pct = max(0, min(100, round(done / expected_per_eq * 100)))
            else:
                pct = 0

            if pct == 100:
                n_concluidos += 1

            any_travado = any(t.get("status") == "travado" for t in tasks)
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

            if done == 0 and expected_per_eq > 0:
                criticos.append(EquipamentoCritico(frota=frota, modelo=modelo, grupo=grupo_nome, pct=0, status="zero"))
            elif any_travado:
                criticos.append(EquipamentoCritico(frota=frota, modelo=modelo, grupo=grupo_nome, pct=pct, status="travado"))

            all_equipamentos.append({
                "frota": frota, "modelo": modelo, "grupo": grupo_nome,
                "pct": pct, "status": status_eq,
            })

    # pct_geral ponderado (mesma fórmula do kpi_engine: sum(done) / sum(expected))
    total_done = 0
    total_expected = 0
    for gid in grupo_ids:
        eq_list = eq_por_grupo.get(gid, [])
        svc_c   = svc_por_grupo.get(gid, 0)
        eq_c    = len(eq_list)
        if eq_c > 0 and svc_c > 0:
            total_done     += done_by_grupo.get(gid, 0)
            total_expected += eq_c * svc_c * 3

    pct_geral = max(0, min(100, round(total_done / max(total_expected, 1) * 100)))
    n_equipamentos = sum(len(v) for v in eq_por_grupo.values())

    # ── evolução semanal ───────────────────────────────────────────────────────
    evolucao: list[SemanaSnapshot] = []
    semana_counts: dict[int, int] = {}
    for t in tarefas:
        sem = int(t.get("semana") or 0)
        if sem <= 0:
            continue
        done_t = int(bool(t.get("etapa_d"))) + int(bool(t.get("etapa_r"))) + int(bool(t.get("etapa_m")))
        semana_counts[sem] = semana_counts.get(sem, 0) + done_t

    cumulative = 0
    for sem in range(1, semana_atual + 1):
        cumulative += semana_counts.get(sem, 0)
        pct_sem = max(0, min(100, round(cumulative / max(total_expected, 1) * 100)))
        evolucao.append(SemanaSnapshot(semana=sem, concluidos=cumulative,
                                       total=total_expected, pct=pct_sem))

    pct_semana_atual    = evolucao[-1].pct if evolucao else pct_geral
    pct_semana_anterior = evolucao[-2].pct if len(evolucao) >= 2 else 0

    # ── alertas ───────────────────────────────────────────────────────────────
    n_travados = n_sem_inicio = n_parados = n_risco_prazo = 0
    esperado_pct = _pct(semana_atual, semanas_total)

    for gid, eqs in eq_por_grupo.items():
        svc_count = svc_por_grupo.get(gid, 0)
        expected_per_eq = svc_count * 3
        for eq in eqs:
            eid   = eq["id"]
            tasks = eq_tasks.get(eid, [])
            done  = sum(int(bool(t.get("etapa_d"))) + int(bool(t.get("etapa_r"))) + int(bool(t.get("etapa_m")))
                        for t in tasks)
            pct   = max(0, min(100, round(done / max(expected_per_eq, 1) * 100))) if expected_per_eq > 0 else 0
            for t in tasks:
                status  = t.get("status") or "pendente"
                updated = t.get("updated_at") or t.get("dt_etapa_m") or t.get("dt_etapa_r") or t.get("dt_etapa_d")
                dias    = _dias_desde(updated)
                if status == "travado" and (dias is None or dias >= dias_travado):
                    n_travados += 1
                if not t.get("etapa_d") and not t.get("etapa_r") and not t.get("etapa_m"):
                    if dias is None or dias >= dias_sem_update:
                        n_sem_inicio += 1
                if status not in ("concluido", "nao_aplica", "travado"):
                    if dias is not None and dias >= dias_sem_update:
                        n_parados += 1
            if expected_per_eq > 0 and pct < esperado_pct - 15 and pct < 100:
                n_risco_prazo += 1

    n_alertas_total = n_travados + (1 if n_risco_prazo else 0)

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
        evolucao=evolucao,
        pct_semana_anterior=pct_semana_anterior,
        pct_semana_atual=pct_semana_atual,
        criticos=sorted(criticos, key=lambda x: x.pct),
        todos_equipamentos=sorted(all_equipamentos, key=lambda e: e["pct"]),
        n_travados=n_travados,
        n_sem_inicio=n_sem_inicio,
        n_parados=n_parados,
        n_risco_prazo=n_risco_prazo,
        primary_color=branding.get("primary_color") or "#FFD100",
        logo_url=branding.get("logo_url"),
    ), sorted(all_equipamentos, key=lambda e: e["pct"])


# ── Resultado do dispatch ─────────────────────────────────────────────────────

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


# ── Função principal ──────────────────────────────────────────────────────────

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
    from src.services.email.recipients import get_recipient_groups, get_admin_recipients
    from src.services.email.smtp_sender import (
        SmtpConfig, EmailMessage, send_email, build_html_body,
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
    revisao     = _load_revisao(sb, revisao_id)
    tenant_nome = _load_tenant_nome(sb, tenant_id)
    branding    = _load_branding(sb, tenant_id)

    if not revisao:
        result.errors.append("Revisão não encontrada.")
        return result

    # Grupos de destinatários
    groups = get_recipient_groups(tenant_id)
    if dept_ids_filter:
        groups = [g for g in groups if g.departamento_id in dept_ids_filter]

    if not groups:
        result.skipped += 1
        result.errors.append("Nenhum departamento com responsável e-mail válido encontrado.")
        return result

    _log(f"Iniciando disparo para {len(groups)} departamento(s)…")

    for grp in groups:
        _log(f"  → Processando departamento: {grp.departamento_nome}")
        try:
            tarefas = _load_tarefas(sb, tenant_id, revisao_id, grp.grupo_ids)
            # Não pula departamentos sem tarefas — podem ter equipamentos com 0% ainda sem início

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
            pdf_name  = (
                f"relatorio_{grp.departamento_nome.lower().replace(' ','_')}"
                f"_semana{payload.semana_atual}.pdf"
            )

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
                    send_email(EmailMessage(
                        to=[rec.email],
                        subject=(f"[{payload.revisao_titulo}] Relatório Semanal — "
                                 f"{grp.departamento_nome} · Semana {payload.semana_atual}"),
                        html_body=html,
                        pdf_bytes=pdf_bytes,
                        pdf_filename=pdf_name,
                    ), cfg=smtp_cfg)
                    result.sent += 1
                    _log(f"    ↳ ✅ Enviado.")
                except Exception as e:
                    result.failed += 1
                    msg = f"Falha ao enviar para {rec.email}: {e}"
                    result.errors.append(msg)
                    _log(f"    ↳ ❌ {msg}")

        except Exception as e:
            result.failed += 1
            msg = f"Erro no departamento {grp.departamento_nome}: {e}"
            result.errors.append(msg)
            _log(f"  ❌ {msg}")

    # ── Relatório consolidado para admins ────────────────────────────────────
    _log("  → Gerando relatório consolidado para admins…")
    try:
        from src.services.email.recipients import get_admin_recipients
        from src.services.reporting.pdf_relatorio_consolidado import (
            build_consolidated_pdf, RelatorioConsolidadoPayload, DeptSummary,
        )
        from src.services.reporting.pdf_relatorio_semanal import EquipamentoCritico

        admin_recs = get_admin_recipients(tenant_id)
        if admin_recs:
            # Monta payload consolidado a partir dos dados já calculados por grupo
            dept_summaries: list[DeptSummary] = []
            for grp in groups:
                try:
                    tarefas_g = _load_tarefas(sb, tenant_id, revisao_id, grp.grupo_ids)
                    if not tarefas_g:
                        continue
                    p, _ = _build_payload(
                        tarefas=tarefas_g, revisao=revisao,
                        departamento_nome=grp.departamento_nome,
                        tenant_nome=tenant_nome, branding=branding,
                        sb=sb, tenant_id=tenant_id, grupo_ids=grp.grupo_ids,
                        dias_travado=dias_travado, dias_sem_update=dias_sem_update,
                    )
                    dept_summaries.append(DeptSummary(
                        nome=grp.departamento_nome,
                        pct_geral=p.pct_geral,
                        n_equipamentos=p.n_equipamentos,
                        n_concluidos=p.n_concluidos,
                        n_travados=p.n_travados,
                        n_sem_inicio=p.n_sem_inicio,
                        n_parados=p.n_parados,
                        n_risco_prazo=p.n_risco_prazo,
                        pct_semana_anterior=p.pct_semana_anterior,
                        pct_semana_atual=p.pct_semana_atual,
                        criticos=p.criticos,
                    ))
                except Exception as e_g:
                    _log(f"    ↳ Aviso: erro ao montar resumo de {grp.departamento_nome}: {e_g}")

            if dept_summaries:
                consol_payload = RelatorioConsolidadoPayload(
                    tenant_nome=tenant_nome or "AgroSafra",
                    revisao_titulo=revisao.get("titulo") or "Revisão",
                    semana_atual=_semana_atual(revisao.get("data_inicio"),
                                               int(revisao.get("semanas_total") or 1)),
                    semanas_total=int(revisao.get("semanas_total") or 1),
                    departamentos=dept_summaries,
                    primary_color=branding.get("primary_color") or "#FFD100",
                    logo_url=branding.get("logo_url"),
                )
                pdf_consol = build_consolidated_pdf(consol_payload)
                pdf_name_c = (f"relatorio_consolidado_semana"
                              f"{consol_payload.semana_atual}.pdf")

                for rec in admin_recs:
                    result.total_emails += 1
                    _log(f"    ↳ Consolidado → {rec.email} ({rec.nome})")
                    if dry_run:
                        _log("    ↳ [DRY RUN] — e-mail não enviado.")
                        result.sent += 1
                        continue
                    try:
                        from src.services.email.smtp_sender import build_html_body, EmailMessage, send_email
                        html_c = build_html_body(
                            destinatario_nome=rec.nome,
                            departamento_nome="Todos os departamentos",
                            revisao_titulo=consol_payload.revisao_titulo,
                            semana_atual=consol_payload.semana_atual,
                            semanas_total=consol_payload.semanas_total,
                            pct_geral=consol_payload.pct_geral,
                            n_alertas=consol_payload.n_alertas_total,
                            primary_color=consol_payload.primary_color,
                        )
                        send_email(EmailMessage(
                            to=[rec.email],
                            subject=(f"[{consol_payload.revisao_titulo}] Relatório Consolidado — "
                                     f"Todos os Departamentos · Semana {consol_payload.semana_atual}"),
                            html_body=html_c,
                            pdf_bytes=pdf_consol,
                            pdf_filename=pdf_name_c,
                        ), cfg=smtp_cfg)
                        result.sent += 1
                        _log("    ↳ ✅ Consolidado enviado.")
                    except Exception as e_send:
                        result.failed += 1
                        msg = f"Falha ao enviar consolidado para {rec.email}: {e_send}"
                        result.errors.append(msg)
                        _log(f"    ↳ ❌ {msg}")
            else:
                _log("    ↳ Sem dados para consolidado — pulando.")
        else:
            _log("    ↳ Nenhum admin com e-mail válido — relatório consolidado não enviado.")
    except Exception as e_consol:
        _log(f"  ⚠️ Erro ao gerar consolidado: {e_consol}")

    _log(f"Concluído: {result.sent} enviados, {result.failed} falhas, {result.skipped} pulados.")
    return result
