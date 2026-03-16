
"""Acesso a dados e operações em lote da tela de revisões."""
from __future__ import annotations

import logging
from collections.abc import Iterable

from postgrest.exceptions import APIError

from src.db.supabase_client import get_supabase_service


log = logging.getLogger(__name__)


def chunked(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _fetch_revisoes_core(client, tenant_id: str, select_fields: str) -> list[dict]:
    return (
        client.table("revisoes")
        .select(select_fields)
        .eq("tenant_id", tenant_id)
        .order("created_at", desc=True)
        .execute()
        .data
    ) or []


def fetch_revisoes(sb, tenant_id: str) -> list[dict]:
    fields = "id,titulo,status,data_inicio,data_fim,semanas_total,created_at"
    try:
        return _fetch_revisoes_core(sb, tenant_id, fields)
    except APIError:
        try:
            return _fetch_revisoes_core(get_supabase_service(), tenant_id, fields)
        except Exception as exc:
            log.warning("Falha ao buscar revisões completas: %s", exc)
            return []


def fetch_revisoes_min(sb, tenant_id: str) -> list[dict]:
    fields = "id,titulo,status,semanas_total,created_at"
    try:
        return _fetch_revisoes_core(sb, tenant_id, fields)
    except APIError:
        try:
            return _fetch_revisoes_core(get_supabase_service(), tenant_id, fields)
        except Exception as exc:
            log.warning("Falha ao buscar revisões resumidas: %s", exc)
            return []


def load_grupos(sb, tenant_id: str) -> list[dict]:
    return (
        sb.table("equip_grupos")
        .select("id,nome")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .order("nome")
        .execute()
        .data
    ) or []


def load_equipamentos(sb, tenant_id: str) -> list[dict]:
    return (
        sb.table("equipamentos")
        .select("id,grupo_id")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .execute()
        .data
    ) or []


def load_grupo_servicos(sb, tenant_id: str, grupo_ids: Iterable[str]) -> dict[str, set]:
    grupo_ids = list(grupo_ids)
    if not grupo_ids:
        return {}
    rows = (
        sb.table("grupo_servicos")
        .select("grupo_id,servico_id")
        .eq("tenant_id", tenant_id)
        .in_("grupo_id", grupo_ids)
        .execute()
        .data
    ) or []
    out: dict[str, set] = {}
    for row in rows:
        out.setdefault(row["grupo_id"], set()).add(row["servico_id"])
    return out


def load_existing_tasks(sb, tenant_id: str, revisao_id: str, equipamento_ids: list[str]) -> dict:
    existing = {}
    for ids in chunked(equipamento_ids, 200):
        rows = (
            sb.table("tarefas_servico")
            .select("id,equipamento_id,servico_id,status")
            .eq("tenant_id", tenant_id)
            .eq("revisao_id", revisao_id)
            .in_("equipamento_id", ids)
            .execute()
            .data
        ) or []
        for row in rows:
            existing.setdefault(row["equipamento_id"], {})[row["servico_id"]] = row
    return existing


def insert_tasks(sb, payload: list[dict]) -> None:
    for batch in chunked(payload, 500):
        sb.table("tarefas_servico").insert(batch).execute()


def update_tasks_status(sb, ids: list[str], status: str = "nao_aplica") -> None:
    for batch in chunked(ids, 500):
        sb.table("tarefas_servico").update({"status": status}).in_("id", batch).execute()
