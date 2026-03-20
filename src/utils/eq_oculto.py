"""Utilitário para equipamentos ocultos por revisão.

Um equipamento oculto não entra no denominador dos KPIs nem aparece
na Matriz. É revelado automaticamente via trigger no banco quando a
primeira etapa (D, R ou M) é marcada.

Uso:
    from src.utils.eq_oculto import get_ocultos, ocultar_equipamento, revelar_equipamento

    # IDs ocultos para uma revisão
    ocultos = get_ocultos(sb, tenant_id, revisao_id)  # set[str]

    # Ocultar (admin/gestor via UI)
    ocultar_equipamento(sb, tenant_id, revisao_id, equipamento_id, user_id)

    # Revelar manualmente (o trigger no banco faz isso automaticamente)
    revelar_equipamento(sb, tenant_id, revisao_id, equipamento_id)
"""
from __future__ import annotations

import logging

log = logging.getLogger("saas.eq_oculto")


def get_ocultos(sb, tenant_id: str, revisao_id: str) -> set[str]:
    """Retorna set de equipamento_ids ocultos nesta revisão."""
    if not (tenant_id and revisao_id):
        return set()
    try:
        rows = (
            sb.table("revisao_equipamento_config")
            .select("equipamento_id")
            .eq("tenant_id", tenant_id)
            .eq("revisao_id", revisao_id)
            .eq("oculto", True)
            .execute()
            .data
        ) or []
        return {r["equipamento_id"] for r in rows if r.get("equipamento_id")}
    except Exception as exc:
        log.warning("get_ocultos falhou: %s", exc)
        return set()


def ocultar_equipamento(
    sb,
    tenant_id: str,
    revisao_id: str,
    equipamento_id: str,
    user_id: str | None = None,
) -> bool:
    """Oculta um equipamento desta revisão. Retorna True se ok."""
    try:
        sb.table("revisao_equipamento_config").upsert({
            "tenant_id":      tenant_id,
            "revisao_id":     revisao_id,
            "equipamento_id": equipamento_id,
            "oculto":         True,
            "oculto_by":      user_id or None,
        }, on_conflict="revisao_id,equipamento_id").execute()
        return True
    except Exception as exc:
        log.error("ocultar_equipamento falhou: %s", exc)
        return False


def revelar_equipamento(
    sb,
    tenant_id: str,
    revisao_id: str,
    equipamento_id: str,
) -> bool:
    """Revela manualmente um equipamento (o trigger faz isso automaticamente)."""
    try:
        sb.table("revisao_equipamento_config").update({"oculto": False}).eq(
            "tenant_id", tenant_id).eq(
            "revisao_id", revisao_id).eq(
            "equipamento_id", equipamento_id).execute()
        return True
    except Exception as exc:
        log.error("revelar_equipamento falhou: %s", exc)
        return False


def is_oculto(
    sb,
    tenant_id: str,
    revisao_id: str,
    equipamento_id: str,
) -> bool:
    """Verifica se um equipamento específico está oculto."""
    return equipamento_id in get_ocultos(sb, tenant_id, revisao_id)
