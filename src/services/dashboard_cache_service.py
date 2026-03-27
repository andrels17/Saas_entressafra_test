from __future__ import annotations

from src.utils.observability import log_error


def refresh_dashboard_cache(sb, tenant_id: str, revisao_id: str) -> bool:
    """Atualiza os caches persistidos da dashboard para uma revisão.

    Retorna True quando a RPC executa sem erro.
    """
    if not tenant_id or not revisao_id:
        return False

    try:
        (
            sb.rpc(
                "refresh_dashboard_entressafra_cache",
                {
                    "p_tenant_id": tenant_id,
                    "p_revisao_id": revisao_id,
                },
            )
            .execute()
        )
        return True
    except Exception as exc:
        log_error(
            exc,
            context="dashboard_cache_service.refresh_dashboard_cache",
            extra={"tenant_id": tenant_id, "revisao_id": revisao_id},
        )
        return False
