
"""Ações destrutivas e resumos operacionais da tela de revisões."""
from __future__ import annotations

import logging

from src.db.supabase_client import get_supabase_service


log = logging.getLogger(__name__)


def safe_count_rows(client, table_name: str, tenant_id: str, revisao_id: str) -> int:
    try:
        resp = (
            client.table(table_name)
            .select("id", count="exact")
            .eq("tenant_id", tenant_id)
            .eq("revisao_id", revisao_id)
            .execute()
        )
        return int(getattr(resp, "count", 0) or 0)
    except Exception:
        try:
            rows = (
                client.table(table_name)
                .select("id")
                .eq("tenant_id", tenant_id)
                .eq("revisao_id", revisao_id)
                .limit(10_000)  # usar fetch_all() se exceder
                .execute()
                .data
            ) or []
            return len(rows)
        except Exception as exc:
            log.warning("Falha ao contar linhas em %s: %s", table_name, exc)
            return 0


def delete_revisao_cascade(tenant_id: str, revisao_id: str) -> dict:
    svc = get_supabase_service()
    result = {"historico": 0, "tarefas": 0, "revisoes": 0}
    result["historico"] = safe_count_rows(svc, "historico_eventos", tenant_id, revisao_id)
    result["tarefas"] = safe_count_rows(svc, "tarefas_servico", tenant_id, revisao_id)

    try:
        svc.table("historico_eventos").delete().eq("tenant_id", tenant_id).eq("revisao_id", revisao_id).execute()
    except Exception as exc:
        log.warning("Falha ao excluir histórico da revisão %s: %s", revisao_id, exc)

    svc.table("tarefas_servico").delete().eq("tenant_id", tenant_id).eq("revisao_id", revisao_id).execute()
    svc.table("revisoes").delete().eq("tenant_id", tenant_id).eq("id", revisao_id).execute()
    result["revisoes"] = 1
    return result


def safe_distinct_task_summary(tenant_id: str, revisao_id: str) -> dict:
    svc = get_supabase_service()
    out = {
        "equipamentos": 0,
        "tarefas_concluidas": 0,
        "tarefas_pendentes": 0,
        "tarefas_total": 0,
        "historico": 0,
    }
    try:
        rows = (
            svc.table("tarefas_servico")
            .select("equipamento_id,status")
            .eq("tenant_id", tenant_id)
            .eq("revisao_id", revisao_id)
            .limit(20_000)  # usar fetch_all() se exceder
            .execute()
            .data
        ) or []
        equipamentos = {r.get("equipamento_id") for r in rows if r.get("equipamento_id") is not None}
        concl = sum(1 for r in rows if r.get("status") == "concluido")
        pend = sum(1 for r in rows if r.get("status") in ("pendente", "em_andamento", "travado"))
        out.update({
            "equipamentos": len(equipamentos),
            "tarefas_concluidas": concl,
            "tarefas_pendentes": pend,
            "tarefas_total": len(rows),
        })
    except Exception as exc:
        log.warning("Falha ao resumir tarefas da revisão %s: %s", revisao_id, exc)

    out["historico"] = safe_count_rows(svc, "historico_eventos", tenant_id, revisao_id)
    return out


def bulk_delete_test_revisions(tenant_id: str, revisoes: list[dict]) -> tuple[int, int, int]:
    total_rev = total_tarefas = total_hist = 0
    for revisao in revisoes:
        res = delete_revisao_cascade(tenant_id, revisao["id"])
        total_rev += int(res.get("revisoes", 0) or 0)
        total_tarefas += int(res.get("tarefas", 0) or 0)
        total_hist += int(res.get("historico", 0) or 0)
    return total_rev, total_tarefas, total_hist
