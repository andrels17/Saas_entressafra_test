from typing import List, Dict, Any


def get_recipients(supabase, tenant_id: str) -> List[Dict[str, Any]]:
    print("🔍 Iniciando busca de recipients...")

    # 1. Buscar preferências
    prefs = (
        supabase.table("tenant_email_prefs")
        .select("user_id, tipo_relatorio, ativo")
        .eq("tenant_id", tenant_id)
        .eq("ativo", True)
        .execute()
        .data
    )

    print("DEBUG prefs:", prefs)

    if not prefs:
        print("⚠️ Nenhuma preferência encontrada")
        return []

    user_ids = [p["user_id"] for p in prefs]
    print("DEBUG user_ids:", user_ids)

    # 2. Buscar usuários direto (SEM depender de departamento)
    users = (
        supabase.table("users")
        .select("id, email, nome")
        .in_("id", user_ids)
        .execute()
        .data
    )

    print("DEBUG users:", users)

    if not users:
        print("⚠️ Nenhum usuário encontrado")
        return []

    # 3. Filtrar quem tem email
    recipients = [u for u in users if u.get("email")]

    print("DEBUG recipients:", recipients)
    print("TOTAL recipients:", len(recipients))

    return recipients
