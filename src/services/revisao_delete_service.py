def delete_revisao_completa(sb, tenant_id: str, revisao_id: str, refresh_matviews: bool = True):
    """Executa a exclusão completa da revisão via RPC."""
    if not tenant_id or not revisao_id:
        raise ValueError("tenant_id e revisao_id são obrigatórios")

    return sb.rpc(
        "delete_revisao_completa",
        {
            "p_tenant_id": tenant_id,
            "p_revisao_id": revisao_id,
            "p_refresh_matviews": refresh_matviews,
        },
    ).execute()
