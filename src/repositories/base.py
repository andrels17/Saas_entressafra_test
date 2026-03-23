"""Repositório base para acesso ao Supabase.

Interface única para queries: safe_select (simples) e safe_select_paginated (paginada).
"""

from __future__ import annotations

from typing import Any

from supabase import Client

from src.utils.observability import log_error


def _apply_filters(q: Any, filters: dict[str, Any]) -> Any:
    """Aplica kwargs de filtro a um query builder Supabase."""
    for key, value in filters.items():
        if value is None:
            continue
        if key.endswith("__eq"):
            q = q.eq(key[:-4], value)
        elif key.endswith("__in"):
            if value:
                q = q.in_(key[:-4], value)
        elif key.endswith("__neq"):
            q = q.neq(key[:-5], value)
        elif key.endswith("__gte"):
            q = q.gte(key[:-5], value)
        elif key.endswith("__lte"):
            q = q.lte(key[:-5], value)
        elif key.endswith("__order"):
            col, desc = value if isinstance(value, tuple) else (value, False)
            q = q.order(col, desc=bool(desc))
        elif key.endswith("__limit"):
            q = q.limit(int(value))
        else:
            q = q.eq(key, value)
    return q


def safe_select(
    sb: Client,
    table: str,
    fields: str,
    **filters: Any,
) -> list[dict]:
    """Executa query Supabase com filtros opcionais e retorna [] em caso de erro."""
    try:
        q = _apply_filters(sb.table(table).select(fields), filters)
        return (q.execute().data) or []
    except Exception as exc:
        log_error(exc, context="repositories.safe_select", table=table)
        return []


def safe_select_paginated(
    sb: Client,
    table: str,
    fields: str,
    page_size: int = 5000,
    max_rows: int = 50_000,
    **filters: Any,
) -> list[dict]:
    """Versão paginada de safe_select usando .range()."""
    out: list[dict] = []
    start = 0

    while True:
        end = start + page_size - 1
        try:
            q = _apply_filters(sb.table(table).select(fields), filters)
            batch = (q.range(start, end).execute().data) or []
        except Exception as exc:
            log_error(
                exc,
                context="repositories.safe_select_paginated",
                table=table,
                extra={"page_start": start, "page_size": page_size},
            )
            break

        out.extend(batch)
        if len(batch) < page_size or len(out) >= max_rows:
            break
        start += page_size

    return out


def fetch_grupo_template(sb: Client, tenant_id: str, grupo_id: str):
    """Busca serviços do template de um grupo, agrupados por setor."""
    from collections import defaultdict

    formats = [
        (
            "servico_id, servicos(id,nome,setor_id,setores(nome))",
            lambda sv: (sv.get("setores") or {}).get("nome") or "Setor",
            "join setor_id+setores",
        ),
        (
            "servico_id, servicos(id,nome,setor)",
            lambda sv: sv.get("setor") or "Setor",
            "campo setor direto",
        ),
    ]

    for select, setor_fn, fmt_label in formats:
        try:
            tpl = (
                sb.table("grupo_servicos").select(select)
                .eq("tenant_id", tenant_id)
                .eq("grupo_id", grupo_id)
                .execute().data
            ) or []
            s2s = defaultdict(list)
            all_s = []
            for r in tpl:
                sv = r.get("servicos") or {}
                sid = sv.get("id")
                if not sid:
                    continue
                s2s[setor_fn(sv)].append(sv)
                all_s.append(sv)
            if all_s:
                return s2s, all_s
        except Exception as exc:
            log_error(
                exc,
                context=f"repositories.fetch_grupo_template[{fmt_label}]",
                table="grupo_servicos",
                extra={"grupo_id": grupo_id, "tenant_id": tenant_id},
            )

    try:
        tpl = (
            sb.table("grupo_servicos").select("servico_id")
            .eq("tenant_id", tenant_id)
            .eq("grupo_id", grupo_id)
            .execute().data
        ) or []
    except Exception as exc:
        log_error(
            exc,
            context="repositories.fetch_grupo_template[fallback-ids]",
            table="grupo_servicos",
            extra={"grupo_id": grupo_id, "tenant_id": tenant_id},
        )
        return defaultdict(list), []

    ids = [r.get("servico_id") for r in tpl if r.get("servico_id")]
    if not ids:
        return defaultdict(list), []

    try:
        svs = (
            sb.table("servicos").select("id,nome,setor")
            .eq("tenant_id", tenant_id)
            .in_("id", ids)
            .execute().data
        ) or []
    except Exception as exc:
        log_error(
            exc,
            context="repositories.fetch_grupo_template[fallback-names]",
            table="servicos",
            extra={"grupo_id": grupo_id, "tenant_id": tenant_id},
        )
        return defaultdict(list), []

    s2s = defaultdict(list)
    all_s = []
    for sv in svs:
        sn = sv.get("setor") or "Setor"
        item = {"id": sv.get("id"), "nome": sv.get("nome")}
        s2s[sn].append(item)
        all_s.append(item)
    return s2s, all_s
