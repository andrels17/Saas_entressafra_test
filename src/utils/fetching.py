"""Utilitários de busca paginada no Supabase (PostgREST).

Nota: para novos módulos prefira safe_select_paginated de src.repositories.base,
que usa kwargs ao invés de uma filters_fn callable.
fetch_all é mantido para auditoria.py que precisa de joins e .order() dinâmicos.
"""
from __future__ import annotations

from typing import Any, Callable

from supabase import Client


def fetch_all(
    sb: Client,
    table: str,
    select: str,
    filters_fn: Callable,
    page_size: int = 5000,
    max_rows: int = 50_000,
) -> list[dict[str, Any]]:
    """Busca todas as linhas de uma tabela com paginação automática via `.range()`.

    Args:
        sb: Cliente Supabase autenticado.
        table: Nome da tabela / view.
        select: Colunas a selecionar (ex.: ``"*"`` ou ``"id,nome"``).
        filters_fn: Callable que recebe o query builder e retorna com filtros aplicados.
        page_size: Linhas por página (default 5000).
        max_rows: Limite máximo de linhas retornadas para evitar OOM (default 50 000).

    Returns:
        Lista de dicts com as linhas encontradas.
    """
    rows: list[dict[str, Any]] = []
    start = 0

    while True:
        end = start + page_size - 1
        try:
            q = sb.table(table).select(select)
            q = filters_fn(q)
            batch = (q.range(start, end).execute().data) or []
        except Exception:
            break  # retorna o que foi coletado até agora
        rows.extend(batch)

        if len(batch) < page_size or len(rows) >= max_rows:
            break
        start += page_size

    return rows
