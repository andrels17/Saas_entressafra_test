"""Carregamento e processamento de dados para notificações.

Responsabilidades:
  - _load_data: query ao Supabase (com cache)
  - _build_alertas: classifica tarefas em categorias de alerta
  - _resumo_por_grupo: consolida contagem por grupo
  - Helpers de data: _semana_atual, _dias_desde
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.db.supabase_client import get_supabase_anon


# ── Helpers de data ──────────────────────────────────────────────────────────

def semana_atual(data_inicio_str: str | None, semanas_total: int) -> int:
    if not data_inicio_str:
        return 1
    try:
        inicio = pd.to_datetime(data_inicio_str, utc=True)
        agora = pd.Timestamp.utcnow()
        semana = max(1, int((agora - inicio).days // 7) + 1)
        return min(semana, semanas_total or semana)
    except Exception:
        return 1


def dias_desde(ts_str: str | None) -> int | None:
    if not ts_str:
        return None
    try:
        ts = pd.to_datetime(ts_str, utc=True)
        return int((pd.Timestamp.utcnow() - ts).total_seconds() // 86400)
    except Exception:
        return None


# ── Query ────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def load_data(tid: str, rev_id: str, ver: str = "0", _token: str = "") -> dict:
    """Carrega todos os dados necessários para os alertas em uma só query."""
    sb = get_supabase_anon()
    if _token:
        sb.postgrest.auth(_token)
    try:
        tarefas = (
            sb.table("tarefas_servico")
            .select(
                "id,equipamento_id,servico_id,status,semana,"
                "etapa_d,etapa_r,etapa_m,observacao,"
                "dt_etapa_d,dt_etapa_r,dt_etapa_m,updated_at,"
                "servicos(id,nome,setor_id,setores(nome)),"
                "equipamentos(id,frota,modelo,grupo_id,"
                "equip_grupos(id,nome,departamento_id))"
            )
            .eq("tenant_id", tid)
            .eq("revisao_id", rev_id)
            .execute()
            .data
        ) or []
    except Exception:
        tarefas = []

    try:
        revisao = (
            sb.table("revisoes")
            .select("id,titulo,data_inicio,semanas_total,status")
            .eq("id", rev_id)
            .limit(1)
            .execute()
            .data
        )
        revisao = revisao[0] if revisao else {}
    except Exception:
        revisao = {}

    return {"tarefas": tarefas, "revisao": revisao}


# ── Processamento de alertas ─────────────────────────────────────────────────

def build_alertas(
    tarefas: list[dict],
    revisao: dict,
    dias_travado: int,
    dias_sem_update: int,
) -> dict:
    """Classifica cada tarefa em categorias de alerta."""
    sem_atual = semana_atual(
        revisao.get("data_inicio"),
        revisao.get("semanas_total") or 99,
    )
    semanas_total = revisao.get("semanas_total") or 99

    travados, sem_inicio, sem_update, risco_prazo = [], [], [], []

    eq_tasks: dict[str, list] = {}
    for t in tarefas:
        eq = (t.get("equipamentos") or {})
        eid = eq.get("id") or t.get("equipamento_id", "")
        eq_tasks.setdefault(eid, []).append(t)

    for eid, tasks in eq_tasks.items():
        eq = (tasks[0].get("equipamentos") or {})
        frota = eq.get("frota") or eid
        modelo = eq.get("modelo") or ""
        grupo = (eq.get("equip_grupos") or {}).get("nome") or "—"
        dept_id = (eq.get("equip_grupos") or {}).get("departamento_id")

        total = len(tasks) * 3
        done = sum(
            int(bool(t.get("etapa_d"))) +
            int(bool(t.get("etapa_r"))) +
            int(bool(t.get("etapa_m")))
            for t in tasks
        )
        pct = round((done / max(total, 1)) * 100)
        esperado_pct = round((sem_atual / max(semanas_total, 1)) * 100)

        base = {
            "Frota": frota, "Modelo": modelo, "Grupo": grupo,
            "dept_id": dept_id, "% Atual": pct, "% Esperado": esperado_pct,
        }

        for t in tasks:
            svc = (t.get("servicos") or {})
            setor = (svc.get("setores") or {}).get("nome") or "—"
            svc_nome = svc.get("nome") or "—"
            status = t.get("status") or "pendente"
            updated = t.get("updated_at") or t.get("dt_etapa_m") or t.get(
                "dt_etapa_r") or t.get("dt_etapa_d")
            dias = dias_desde(updated)

            row = {**base, "Setor": setor, "Serviço": svc_nome,
                   "Status": status, "Obs.": t.get("observacao") or ""}

            if status == "travado" and (dias is None or dias >= dias_travado):
                travados.append({**row, "Dias travado": dias if dias is not None else "?"})

            # sem_inicio: nenhuma etapa marcada.
            # Usa dt_etapa para nao confundir com updated_at de criacao da tarefa.
            # So alerta se a revisao ja tem pelo menos dias_sem_update dias corridos.
            nenhuma_etapa = (
                not t.get("etapa_d") and not t.get("etapa_r") and not t.get("etapa_m")
            )
            dt_real = (t.get("dt_etapa_m") or t.get("dt_etapa_r") or t.get("dt_etapa_d"))
            dias_real = dias_desde(dt_real)
            dias_revisao = dias_desde(revisao.get("data_inicio"))

            if nenhuma_etapa and status not in ("concluido", "nao_aplica"):
                if dias_revisao is not None and dias_revisao >= dias_sem_update:
                    sem_inicio.append({**row, "Dias sem update": dias_revisao})

            # sem_update: tarefa sem atualização recente, mesmo que ainda sem início.
            if status not in ("concluido", "nao_aplica", "travado"):
                if dias is not None and dias >= dias_sem_update:
                    sem_update.append({**row, "Dias parado": dias})

        if pct < esperado_pct - 15 and pct < 100:
            atraso = esperado_pct - pct
            risco_prazo.append({
                **base, "Atraso (p.p.)": atraso,
                "Etapas feitas": done, "Etapas total": total,
            })

    return {
        "travados":    pd.DataFrame(travados)    if travados    else pd.DataFrame(),
        "sem_inicio":  pd.DataFrame(sem_inicio)  if sem_inicio  else pd.DataFrame(),
        "sem_update":  pd.DataFrame(sem_update)  if sem_update  else pd.DataFrame(),
        "risco_prazo": pd.DataFrame(risco_prazo) if risco_prazo else pd.DataFrame(),
        "semana_atual":   sem_atual,
        "semanas_total":  semanas_total,
    }


def resumo_por_grupo(alertas: dict) -> pd.DataFrame:
    """Consolida contagem de alertas por grupo."""
    grupos: dict[str, dict] = {}
    for categoria, col in [
        ("travados",    "travados"),
        ("sem_inicio",  "sem_inicio"),
        ("sem_update",  "parados"),
        ("risco_prazo", "risco_prazo"),
    ]:
        df = alertas.get(categoria, pd.DataFrame())
        if df.empty or "Grupo" not in df.columns:
            continue
        for grupo, cnt in df["Grupo"].value_counts().items():
            grupos.setdefault(str(grupo), {
                "Grupo": str(grupo), "Travados": 0,
                "Sem início": 0, "Parados": 0, "Risco prazo": 0,
            })
            key_map = {
                "travados": "Travados", "sem_inicio": "Sem início",
                "sem_update": "Parados", "risco_prazo": "Risco prazo",
            }
            grupos[str(grupo)][key_map[categoria]] = int(cnt)
    if not grupos:
        return pd.DataFrame()
    df_res = pd.DataFrame(list(grupos.values()))
    df_res["Total alertas"] = df_res[["Travados", "Sem início", "Parados", "Risco prazo"]].sum(axis=1)
    return df_res.sort_values("Total alertas", ascending=False).reset_index(drop=True)
