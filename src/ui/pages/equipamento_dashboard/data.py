"""Carregamento de dados para o Dashboard por Equipamento."""
from __future__ import annotations

import streamlit as st

from src.db.supabase_client import get_supabase_anon


def _sb(token: str = ""):
    sb = get_supabase_anon()
    if token:
        sb.postgrest.auth(token)
    return sb


@st.cache_data(ttl=60, show_spinner=False)
def search_equipamentos(
    tenant_id: str,
    query: str,
    token_key: str = "",
    _token: str = "",
) -> list[dict]:
    """Busca equipamentos por frota ou modelo (case-insensitive, parcial).

    Retorna lista com id, frota, modelo, grupo_nome, departamento_nome, ativo.
    Limitado a 30 resultados para não sobrecarregar a sidebar.
    """
    if not query or len(query.strip()) < 2:
        return []

    sb = _sb(_token)
    q = query.strip()

    try:
        rows = (
            sb.table("equipamentos")
            .select(
                "id,frota,modelo,ativo,grupo_id,"
                "equip_grupos(nome,departamentos(nome))"
            )
            .eq("tenant_id", tenant_id)
            .or_(f"frota.ilike.%{q}%,modelo.ilike.%{q}%")
            .order("frota")
            .limit(30)
            .execute()
            .data
        ) or []
    except Exception:
        return []

    out = []
    for r in rows:
        grp = r.get("equip_grupos") or {}
        dept = grp.get("departamentos") or {}
        out.append({
            "id": str(r.get("id") or ""),
            "frota": r.get("frota") or "—",
            "modelo": r.get("modelo") or "—",
            "ativo": r.get("ativo", True),
            "grupo_nome": grp.get("nome") or "Sem grupo",
            "departamento_nome": dept.get("nome") or "—",
        })
    return out


@st.cache_data(ttl=30, show_spinner=False)
def load_equipamento_detail(
    tenant_id: str,
    equipamento_id: str,
    token_key: str = "",
    _token: str = "",
) -> dict | None:
    """Carrega dados completos de um equipamento pelo ID."""
    sb = _sb(_token)
    try:
        rows = (
            sb.table("equipamentos")
            .select(
                "id,frota,modelo,ativo,grupo_id,"
                "equip_grupos(id,nome,departamento_id,departamentos(nome))"
            )
            .eq("tenant_id", tenant_id)
            .eq("id", equipamento_id)
            .limit(1)
            .execute()
            .data
        ) or []
    except Exception:
        return None

    if not rows:
        return None

    r = rows[0]
    grp = r.get("equip_grupos") or {}
    dept = grp.get("departamentos") or {}
    return {
        "id": str(r.get("id") or ""),
        "frota": r.get("frota") or "—",
        "modelo": r.get("modelo") or "—",
        "ativo": r.get("ativo", True),
        "grupo_id": str(grp.get("id") or ""),
        "grupo_nome": grp.get("nome") or "Sem grupo",
        "departamento_id": str(grp.get("departamento_id") or ""),
        "departamento_nome": dept.get("nome") or "—",
    }


@st.cache_data(ttl=20, show_spinner=False)
def load_tarefas_equipamento(
    tenant_id: str,
    equipamento_id: str,
    revisao_id: str,
    token_key: str = "",
    _token: str = "",
) -> list[dict]:
    """Carrega todas as tarefas de um equipamento na revisão ativa."""
    sb = _sb(_token)
    try:
        return (
            sb.table("tarefas_servico")
            .select(
                "id,status,semana,etapa_d,etapa_r,etapa_m,"
                "observacao,updated_at,dt_etapa_d,dt_etapa_r,dt_etapa_m,"
                "servicos(id,nome,setores(nome))"
            )
            .eq("tenant_id", tenant_id)
            .eq("revisao_id", revisao_id)
            .eq("equipamento_id", equipamento_id)
            .order("updated_at", desc=True)
            .execute()
            .data
        ) or []
    except Exception:
        return []


@st.cache_data(ttl=60, show_spinner=False)
def load_revisao_ativa(
    tenant_id: str,
    token_key: str = "",
    _token: str = "",
) -> dict | None:
    """Busca a revisão ativa do tenant."""
    sb = _sb(_token)
    try:
        rows = (
            sb.table("revisoes")
            .select("id,titulo,status,data_inicio,data_fim,semanas_total")
            .eq("tenant_id", tenant_id)
            .eq("status", "ativa")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
            .data
        ) or []
        return rows[0] if rows else None
    except Exception:
        return None


@st.cache_data(ttl=60, show_spinner=False)
def load_historico_revisoes(
    tenant_id: str,
    equipamento_id: str,
    token_key: str = "",
    _token: str = "",
) -> list[dict]:
    """Busca histórico de KPIs do equipamento nas últimas 5 revisões encerradas."""
    sb = _sb(_token)
    try:
        # Busca revisões encerradas
        revisoes = (
            sb.table("revisoes")
            .select("id,titulo,status")
            .eq("tenant_id", tenant_id)
            .in_("status", ["fechada", "concluida", "encerrada", "arquivada"])
            .order("created_at", desc=True)
            .limit(5)
            .execute()
            .data
        ) or []

        if not revisoes:
            return []

        rev_ids = [str(r["id"]) for r in revisoes]
        rev_map = {str(r["id"]): r.get("titulo", "—") for r in revisoes}

        tarefas = (
            sb.table("tarefas_servico")
            .select("revisao_id,status,etapa_d,etapa_r,etapa_m")
            .eq("tenant_id", tenant_id)
            .eq("equipamento_id", equipamento_id)
            .in_("revisao_id", rev_ids)
            .execute()
            .data
        ) or []

        # Agrupa por revisão
        from collections import defaultdict
        by_rev: dict[str, dict] = defaultdict(lambda: {"total": 0, "done": 0})
        for t in tarefas:
            rid = str(t.get("revisao_id") or "")
            by_rev[rid]["total"] += 3  # 3 etapas por tarefa
            by_rev[rid]["done"] += (
                int(bool(t.get("etapa_d")))
                + int(bool(t.get("etapa_r")))
                + int(bool(t.get("etapa_m")))
            )

        result = []
        for rid in rev_ids:
            data = by_rev.get(rid, {"total": 0, "done": 0})
            total = data["total"]
            done = data["done"]
            pct = round((done / max(total, 1)) * 100)
            result.append({
                "revisao_id": rid,
                "titulo": rev_map.get(rid, "—"),
                "pct": pct,
                "total": total,
                "done": done,
            })
        return result
    except Exception:
        return []
