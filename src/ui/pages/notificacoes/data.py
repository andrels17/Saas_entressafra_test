"""Carregamento e processamento de dados para notificações.

Responsabilidades:
  - _load_data: query ao Supabase (com cache)
  - _build_alertas: classifica tarefas em categorias de alerta
  - _resumo_por_grupo: consolida contagem por grupo
  - Helpers de data: _semana_atual, _dias_desde
"""
from __future__ import annotations

import io
import zipfile
from collections import defaultdict

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


# ── Impressão por gestor / grupo ─────────────────────────────────────────────

def _slug(value: str) -> str:
    value = str(value or '').strip().replace('/', '-').replace('\\', '-')
    cleaned = ''.join(ch if ch.isalnum() or ch in ('-', '_', ' ') else '_' for ch in value)
    return '_'.join(cleaned.split()) or 'arquivo'


@st.cache_data(ttl=60, show_spinner=False)
def load_manager_group_options(tid: str, ver: str = '0', _token: str = '') -> list[dict]:
    """Retorna gestores e seus grupos, priorizando tenant_user_departamentos.

    Regras:
      - gestores são users com role `gestor` ou `manager` em tenant_users
      - se tenant_user_departamentos.grupo_id vier preenchido, usa o grupo explícito
      - se vier apenas departamento_id, expande para todos os grupos do departamento
      - também lê tenant_user_scope como fallback legado
    """
    sb = get_supabase_anon()
    if _token:
        sb.postgrest.auth(_token)

    try:
        tu_rows = (
            sb.table('tenant_users')
            .select('user_id,role')
            .eq('tenant_id', tid)
            .execute()
            .data
        ) or []
    except Exception:
        tu_rows = []

    gestor_ids = {
        str(r.get('user_id'))
        for r in tu_rows
        if str(r.get('role') or '').strip().lower() in {'gestor', 'manager'} and r.get('user_id')
    }
    if not gestor_ids:
        return []

    try:
        scope_rows = (
            sb.table('tenant_user_departamentos')
            .select('user_id,departamento_id,grupo_id')
            .eq('tenant_id', tid)
            .execute()
            .data
        ) or []
    except Exception:
        scope_rows = []

    try:
        legacy_rows = (
            sb.table('tenant_user_scope')
            .select('user_id,departamento_id,grupo_id')
            .eq('tenant_id', tid)
            .execute()
            .data
        ) or []
    except Exception:
        legacy_rows = []

    try:
        dep_rows = (
            sb.table('departamentos')
            .select('id,nome,ativo')
            .eq('tenant_id', tid)
            .execute()
            .data
        ) or []
    except Exception:
        dep_rows = []
    dep_map = {str(r.get('id')): r.get('nome') or str(r.get('id')) for r in dep_rows if r.get('id')}

    try:
        grp_rows = (
            sb.table('equip_grupos')
            .select('id,nome,departamento_id,ativo')
            .eq('tenant_id', tid)
            .execute()
            .data
        ) or []
    except Exception:
        grp_rows = []

    active_groups = []
    for r in grp_rows:
        ativo = r.get('ativo')
        if ativo is False:
            continue
        gid = r.get('id')
        if gid:
            active_groups.append(r)
    grp_map = {str(r.get('id')): r for r in active_groups if r.get('id')}
    dep_to_groups: dict[str, list[dict]] = defaultdict(list)
    for g in active_groups:
        dep_id = g.get('departamento_id')
        if dep_id:
            dep_to_groups[str(dep_id)].append(g)

    profile_map = {}
    try:
        prof_rows = (
            sb.table('user_profiles')
            .select('user_id,nome')
            .in_('user_id', list(gestor_ids))
            .execute()
            .data
        ) or []
        profile_map = {str(r.get('user_id')): r.get('nome') or '' for r in prof_rows if r.get('user_id')}
    except Exception:
        profile_map = {}

    user_to_groups: dict[str, dict[str, dict]] = defaultdict(dict)

    def _attach(uid: str | None, dep_id: str | None, grp_id: str | None):
        uid = str(uid or '')
        if not uid or uid not in gestor_ids:
            return
        if grp_id:
            grp = grp_map.get(str(grp_id))
            if not grp:
                return
            dep_eff = str(grp.get('departamento_id') or dep_id or '')
            user_to_groups[uid][str(grp['id'])] = {
                'grupo_id': str(grp['id']),
                'grupo_nome': grp.get('nome') or str(grp['id']),
                'departamento_id': dep_eff or None,
                'departamento_nome': dep_map.get(dep_eff, dep_eff) if dep_eff else '—',
            }
            return
        if dep_id:
            for grp in dep_to_groups.get(str(dep_id), []):
                user_to_groups[uid][str(grp['id'])] = {
                    'grupo_id': str(grp['id']),
                    'grupo_nome': grp.get('nome') or str(grp['id']),
                    'departamento_id': str(dep_id),
                    'departamento_nome': dep_map.get(str(dep_id), str(dep_id)),
                }

    for row in scope_rows + legacy_rows:
        _attach(row.get('user_id'), row.get('departamento_id'), row.get('grupo_id'))

    out = []
    for uid in sorted(user_to_groups.keys(), key=lambda x: (profile_map.get(x) or '').lower()):
        groups = sorted(
            user_to_groups[uid].values(),
            key=lambda g: ((g.get('departamento_nome') or '').lower(), (g.get('grupo_nome') or '').lower()),
        )
        if not groups:
            continue
        out.append({
            'user_id': uid,
            'gestor_nome': profile_map.get(uid) or f'Gestor {uid[:8]}',
            'grupos': groups,
        })
    return out


def build_manager_groups_zip(
    tid: str,
    revisao_id: str,
    revisao_titulo: str,
    selected_items: list[dict],
    ver: str = '0',
    _token: str = '',
) -> bytes:
    """Gera um ZIP com 1 PDF da matriz por grupo selecionado."""
    if not selected_items:
        return b''

    from src.ui.pages.matriz_modular.data import _load_payload
    from src.ui.pages.matriz_modular.context import (
        _build_eq_labels,
        _build_resumo_df,
        _build_sector_tables_for_export,
    )
    from src.ui.pages.matriz_modular.pdf_export import _build_pdf_tables

    sb = get_supabase_anon()
    if _token:
        sb.postgrest.auth(_token)

    rev_title = revisao_titulo or 'revisao'
    try:
        rev_rows = (
            sb.table('revisoes')
            .select('titulo')
            .eq('id', revisao_id)
            .limit(1)
            .execute()
            .data
        ) or []
        if rev_rows and rev_rows[0].get('titulo'):
            rev_title = rev_rows[0]['titulo']
    except Exception:
        pass

    group_name_map = {}
    try:
        gids = list({str(it.get('grupo_id')) for it in selected_items if it.get('grupo_id')})
        if gids:
            grp_rows = (
                sb.table('equip_grupos')
                .select('id,nome')
                .eq('tenant_id', tid)
                .in_('id', gids)
                .execute()
                .data
            ) or []
            group_name_map = {str(r.get('id')): r.get('nome') or str(r.get('id')) for r in grp_rows if r.get('id')}
    except Exception:
        group_name_map = {}

    pdf_cache: dict[str, tuple[bytes, str]] = {}
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for item in selected_items:
            gid = str(item.get('grupo_id') or '')
            gestor_nome = str(item.get('gestor_nome') or 'Gestor')
            if not gid:
                continue
            grupo_nome = group_name_map.get(gid) or str(item.get('grupo_nome') or gid)

            if gid not in pdf_cache:
                payload = _load_payload(tid, gid, revisao_id, 10000, ver, _token) or {}
                eqs = payload.get('eqs') or []
                tarefas = payload.get('tarefas') or []
                setor_to_services = payload.get('s2s') or {}
                all_services = payload.get('all_s') or []
                if not eqs or not all_services:
                    continue
                task_map = {(str(t['equipamento_id']), str(t['servico_id'])): t for t in tarefas if t.get('equipamento_id') and t.get('servico_id')}
                eq_label, eq_label_short = _build_eq_labels(eqs, set())
                resumo_df, *_ = _build_resumo_df(eqs, all_services, task_map, eq_label)
                sector_tables = _build_sector_tables_for_export(eqs, setor_to_services, task_map, eq_label_short)
                if not sector_tables:
                    continue
                pdf_bytes = _build_pdf_tables(
                    titulo=rev_title,
                    grupo_nome=grupo_nome,
                    resumo_df=resumo_df,
                    sector_tables=[(n, df.copy()) for n, df in sector_tables],
                    tarefas_servico_df=pd.DataFrame(tarefas),
                    revisao_id=revisao_id,
                )
                pdf_cache[gid] = (pdf_bytes, grupo_nome)

            cached = pdf_cache.get(gid)
            if not cached:
                continue
            pdf_bytes, grupo_nome = cached
            fname = f"{_slug(rev_title)}__{_slug(gestor_nome)}__{_slug(grupo_nome)}.pdf"
            zf.writestr(fname, pdf_bytes)

    return zip_buffer.getvalue()
