
from __future__ import annotations

import streamlit as st


def risk_color(pct: int) -> str:
    if pct >= 80:
        return "#12B76A"
    if pct >= 50:
        return "#F59E0B"
    return "#EF4444"


def risk_score(pct: int) -> int:
    score = 100 - int(pct)
    if pct < 50:
        score += 15
    if pct < 30:
        score += 20
    return int(score)


@st.cache_data(ttl=60, show_spinner=False)
def build_task_maps(tarefas_json: str):
    """Pré-computa mapas O(1) a partir das tarefas serializadas."""
    import json

    tarefas = json.loads(tarefas_json or "[]")
    task_map = {}
    obs_map = {}
    status_map = {}
    week_map = {}

    for t in tarefas:
        eid = t.get("equipamento_id")
        sid = t.get("servico_id")
        if not eid or not sid:
            continue
        key = (eid, sid)
        task_map[key] = t
        obs = t.get("observacao")
        if obs:
            obs_map[key] = obs
        status_map[key] = t.get("status") or "pendente"
        week_map[key] = t.get("semana")

    return task_map, obs_map, status_map, week_map


def svc_name_map(servicos: list[dict]) -> dict[str, str]:
    return {str(s.get("id")): s.get("nome", "") for s in servicos if s.get("id")}


def eq_label_map(equipamentos: list[dict]) -> dict[str, str]:
    out = {}
    for e in equipamentos:
        eid = str(e.get("id") or "")
        if not eid:
            continue
        out[eid] = str(e.get("frota") or e.get("codigo") or eid)
    return out


def task_key(equipamento_id, servico_id):
    return (str(equipamento_id), str(servico_id))


def sector_lazy_key(revisao_id, grupo_id, setor_nome: str) -> str:
    return f"mtz_sector_open::{revisao_id}::{grupo_id}::{setor_nome}"


def sector_is_open(revisao_id, grupo_id, setor_nome: str) -> bool:
    return bool(st.session_state.get(sector_lazy_key(revisao_id, grupo_id, setor_nome), False))


def sector_set_open(revisao_id, grupo_id, setor_nome: str, value: bool = True) -> None:
    st.session_state[sector_lazy_key(revisao_id, grupo_id, setor_nome)] = bool(value)


@st.cache_data(ttl=60, show_spinner=False)
def filter_obs_map_for_sector(obs_items_json: str, eq_ids_json: str, svc_ids_json: str) -> dict[str, str]:
    """Filtra observações por setor com cache, evitando varrer todas as tarefas a cada rerun."""
    import json

    obs_items = json.loads(obs_items_json or "[]")
    eq_ids = set(json.loads(eq_ids_json or "[]"))
    svc_ids = set(json.loads(svc_ids_json or "[]"))

    return {
        f"{eid}__{sid}": obs
        for eid, sid, obs in obs_items
        if str(eid) in eq_ids and str(sid) in svc_ids and obs
    }


@st.cache_data(ttl=60, show_spinner=False)
def normalize_service_ids(servicos_json: str) -> list[str]:
    """Normaliza ids de serviços para uso estável em filtros e colunas."""
    import json
    servicos = json.loads(servicos_json or "[]")
    return [str(s.get("id")) for s in servicos if s.get("id")]


def bulk_update_tasks(sb, updates: list[dict], *, chunk_size: int = 200) -> tuple[int, int]:
    """Aplica updates em lote via upsert; cai para update individual se necessário.

    Retorna (ok, failed).
    """
    if not updates:
        return 0, 0

    ok = 0
    failed = 0
    for i in range(0, len(updates), chunk_size):
        chunk = updates[i:i + chunk_size]
        try:
            sb.table("tarefas_servico").upsert(chunk, on_conflict="id").execute()
            ok += len(chunk)
        except Exception:
            for row in chunk:
                row = dict(row)
                tid = row.pop("id", None)
                if not tid:
                    failed += 1
                    continue
                try:
                    sb.table("tarefas_servico").update(row).eq("id", tid).execute()
                    ok += 1
                except Exception:
                    failed += 1
    return ok, failed
