from __future__ import annotations

from datetime import date

from src.db.supabase_client import get_supabase_service


SETORES = ["Mecânica", "Elétrica", "Hidráulica"]

SERVICOS = {
    "Mecânica": ["Motor", "Freios", "Câmbio", "Diferencial"],
    "Elétrica": ["Bateria", "Alternador", "Iluminação"],
    "Hidráulica": ["Bombas", "Mangueiras", "Vazamentos"],
}

GRUPOS = ["Tratores Transbordos", "Caminhões"]

EQUIPAMENTOS = {
    "Tratores Transbordos": [
        ("2055", "John Deere 6190J"),
        ("2056", "John Deere 6190J"),
        ("2067", "John Deere 6190M"),
    ],
    "Caminhões": [
        ("3101", "VW Constellation"),
        ("3102", "MB Atego"),
    ],
}


def _get_or_create_setor(svc, tenant_id: str, nome: str) -> str:
    found = (
        svc.table("setores")
        .select("id")
        .eq("tenant_id", tenant_id)
        .eq("nome", nome)
        .limit(1)
        .execute()
        .data
    ) or []
    if found:
        return found[0]["id"]
    row = svc.table("setores").insert({"tenant_id": tenant_id, "nome": nome, "ativo": True}).execute().data
    return row[0]["id"]


def _get_or_create_servico(svc, tenant_id: str, setor_id: str, nome: str) -> str:
    found = (
        svc.table("servicos")
        .select("id")
        .eq("tenant_id", tenant_id)
        .eq("setor_id", setor_id)
        .eq("nome", nome)
        .limit(1)
        .execute()
        .data
    ) or []
    if found:
        return found[0]["id"]
    row = svc.table("servicos").insert({"tenant_id": tenant_id, "setor_id": setor_id, "nome": nome, "ativo": True}).execute().data
    return row[0]["id"]


def _get_or_create_grupo(svc, tenant_id: str, nome: str) -> str:
    found = (
        svc.table("equip_grupos")
        .select("id")
        .eq("tenant_id", tenant_id)
        .eq("nome", nome)
        .limit(1)
        .execute()
        .data
    ) or []
    if found:
        return found[0]["id"]
    row = svc.table("equip_grupos").insert({"tenant_id": tenant_id, "nome": nome, "ativo": True}).execute().data
    return row[0]["id"]


def _get_or_create_equipamento(svc, tenant_id: str, grupo_id: str, frota: str, modelo: str) -> str:
    found = (
        svc.table("equipamentos")
        .select("id")
        .eq("tenant_id", tenant_id)
        .eq("frota", frota)
        .limit(1)
        .execute()
        .data
    ) or []
    if found:
        # ensure group/model/ativo updated for demo
        svc.table("equipamentos").update({"grupo_id": grupo_id, "modelo": modelo, "ativo": True}).eq("id", found[0]["id"]).execute()
        return found[0]["id"]
    row = svc.table("equipamentos").insert({
        "tenant_id": tenant_id,
        "grupo_id": grupo_id,
        "frota": frota,
        "modelo": modelo,
        "ativo": True,
    }).execute().data
    return row[0]["id"]


def seed_demo_data(tenant_id: str) -> dict:
    """Idempotent-ish demo seed. Uses Service Role (server-side)."""
    svc = get_supabase_service()

    # setores + serviços
    setor_ids = {}
    servico_ids = []
    for setor in SETORES:
        sid = _get_or_create_setor(svc, tenant_id, setor)
        setor_ids[setor] = sid
        for nome in SERVICOS.get(setor, []):
            servico_ids.append(_get_or_create_servico(svc, tenant_id, sid, nome))

    # grupos + equipamentos
    grupo_ids = {}
    equipamento_ids = []
    for g in GRUPOS:
        gid = _get_or_create_grupo(svc, tenant_id, g)
        grupo_ids[g] = gid
        for frota, modelo in EQUIPAMENTOS.get(g, []):
            equipamento_ids.append(_get_or_create_equipamento(svc, tenant_id, gid, frota, modelo))

    # templates: vincula todos os serviços a todos os grupos (pode refinar depois)
    # evita duplicar via select-check
    for gid in grupo_ids.values():
        existing = (
            svc.table("grupo_servicos")
            .select("servico_id")
            .eq("tenant_id", tenant_id)
            .eq("grupo_id", gid)
            .execute()
            .data
        ) or []
        existing_ids = {r["servico_id"] for r in existing}
        to_add = [{"tenant_id": tenant_id, "grupo_id": gid, "servico_id": sid} for sid in servico_ids if sid not in existing_ids]
        if to_add:
            # batch insert
            for i in range(0, len(to_add), 500):
                svc.table("grupo_servicos").insert(to_add[i:i+500]).execute()

    # revisão demo: fecha outras ativas e cria/garante uma ativa
    demo_title = "Revisão Demo"
    existing_demo = (
        svc.table("revisoes")
        .select("id,status")
        .eq("tenant_id", tenant_id)
        .eq("titulo", demo_title)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
    ) or []

    # close any active to keep one active
    svc.table("revisoes").update({"status": "fechada"}).eq("tenant_id", tenant_id).eq("status", "ativa").execute()

    if existing_demo:
        rid = existing_demo[0]["id"]
        svc.table("revisoes").update({"status": "ativa"}).eq("id", rid).execute()
    else:
        rid = svc.table("revisoes").insert({
            "tenant_id": tenant_id,
            "titulo": demo_title,
            "status": "ativa",
            "data_inicio": str(date.today()),
            "semanas_total": 12,
        }).execute().data[0]["id"]

    return {
        "setores": len(SETORES),
        "servicos": len(servico_ids),
        "grupos": len(grupo_ids),
        "equipamentos": len(equipamento_ids),
        "revisao_id": rid,
    }
