"""Repositório base para acesso ao Supabase.

Interface única para queries: safe_select (simples) e safe_select_paginated (paginada).

Melhorias v2:
  - Todos os `except Exception` agora chamam log_error() de observability.py
  - Erros de banco deixam rastro estruturado (tabela, contexto, tenant) nos logs
  - Interface pública preservada: nenhum módulo existente precisa mudar

Uso:
    from src.repositories.base import safe_select, safe_select_paginated

    rows = safe_select(sb, "equipamentos", "id,nome", tenant_id__eq="abc", ativo__eq=True)

Convenção de filtros via kwargs:
  - campo__eq    → .eq(campo, valor)
  - campo__in    → .in_(campo, lista)
  - campo__neq   → .neq(campo, valor)
  - campo__gte   → .gte(campo, valor)
  - campo__lte   → .lte(campo, valor)
  - campo__order → .order(campo, desc=False) | (campo, True) para desc
  - campo__limit → .limit(valor)
  - campo        → atalho para .eq(campo, valor)
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
    """Executa uma query Supabase com filtros opcionais e retorna [] em caso de erro.

    Diferente da versão anterior: erros são sempre logados com contexto estruturado,
    nunca silenciados. O comportamento externo (retorno de []) é idêntico.
    """
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
