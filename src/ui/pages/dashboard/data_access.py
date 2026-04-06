"""Helpers de acesso a dados do dashboard.

Separados da camada de renderização para reduzir o tamanho do módulo principal
e facilitar testes das rotinas de carga com fallback.
"""
from __future__ import annotations

import hashlib

from src.db.supabase_client import get_supabase_anon


def _sb_from_token(token: str = ""):
    sb = get_supabase_anon()
    if token:
        sb.postgrest.auth(token)
    return sb

def _token_cache_key(token: str = "") -> str:
    return hashlib.md5((token or "").encode()).hexdigest()[:8]


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




def _load_task_rows_with_fallback(sb, tenant_id: str, revisao_id: str, fetch_all) -> tuple[list, dict]:
    """Carrega tarefas do dashboard tentando leitura direta primeiro e RPC depois.

    Motivo: quando a RPC existir mas retornar [] por configuração incorreta,
    não podemos deixar isso zerar o dashboard inteiro de perfis que já possuem
    leitura direta válida na tabela.
    """
    meta = {
        "task_source": "table",
        "task_rpc_used": None,
        "task_rpc_available": False,
        "task_load_error": "",
    }

    try:
        rows = fetch_all(
            sb.table("tarefas_servico")
            .select("equipamento_id,servico_id,status,etapa_d,etapa_r,etapa_m,updated_at,revisao_id")
            .eq("tenant_id", tenant_id)
            .eq("revisao_id", revisao_id)
        )
        if rows:
            return rows, meta
    except Exception as exc:
        meta["task_load_error"] = str(exc)

    rpc_candidates = [
        ("get_tarefas_dashboard", {"p_tenant_id": tenant_id, "p_revisao_id": revisao_id}),
        ("get_tarefas_servico_dashboard", {"p_tenant_id": tenant_id, "p_revisao_id": revisao_id}),
        ("get_dashboard_tarefas", {"p_tenant_id": tenant_id, "p_revisao_id": revisao_id}),
    ]

    def _fetch_all_rpc(rpc_name: str, rpc_params: dict, page_size: int = 1000) -> list:
        rows = []
        start = 0
        while True:
            chunk = (
                sb.rpc(rpc_name, rpc_params)
                .range(start, start + page_size - 1)
                .execute()
                .data
                or []
            )
            rows.extend(chunk)
            if len(chunk) < page_size:
                break
            start += page_size
        return rows

    required_cols = {"equipamento_id", "servico_id", "status"}
    for rpc_name, rpc_params in rpc_candidates:
        try:
            rpc_rows = _fetch_all_rpc(rpc_name, rpc_params)
            if rpc_rows and required_cols.issubset(set(rpc_rows[0].keys())):
                meta["task_source"] = "rpc"
                meta["task_rpc_used"] = rpc_name
                meta["task_rpc_available"] = True
                return rpc_rows, meta
            if rpc_rows == []:
                meta["task_rpc_used"] = rpc_name
                meta["task_rpc_available"] = True
        except Exception as exc:
            meta["task_load_error"] = str(exc)
            continue

    return [], meta
