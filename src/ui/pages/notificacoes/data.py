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


# ── ZIP para impressão por gestor ───────────────────────────────────────────

import io
import re
import zipfile
from collections import defaultdict


def _slugify(value: str) -> str:
    value = str(value or "").strip()
    value = re.sub(r"[^A-Za-z0-9À-ÿ._ -]+", "", value)
    value = value.replace("/", "-").replace(chr(92), "-")
    value = re.sub(r"\s+", "_", value)
    return value or "item"


def _load_profiles_map(svc, user_ids: list[str]) -> dict[str, str]:
    if not user_ids:
        return {}
    try:
        rows = (
            svc.table("user_profiles")
            .select("user_id,nome")
            .in_("user_id", user_ids)
            .execute()
            .data
        ) or []
        return {str(r.get("user_id")): (r.get("nome") or "") for r in rows if r.get("user_id")}
    except Exception:
        return {}


@st.cache_data(ttl=60, show_spinner=False)
def load_manager_print_options(tid: str, ver: str = "0", _token: str = "") -> list[dict]:
    """Retorna gestores e grupos disponíveis para geração do ZIP de impressão.

    Importante: esta função não depende de e-mail nem de auth.admin.
    Ela usa tenant_users + tenant_user_departamentos/tenant_user_scope +
    equip_grupos, que é o que de fato define o vínculo operacional.
    """
    try:
        from src.db.supabase_client import get_supabase_service
        svc = get_supabase_service()
    except Exception:
        return []

    try:
        tu_rows = (
            svc.table("tenant_users")
            .select("user_id,role")
            .eq("tenant_id", tid)
            .execute()
            .data
        ) or []
    except Exception:
        tu_rows = []

    role_map: dict[str, str] = {}
    gestor_uids: set[str] = set()
    for row in tu_rows:
        uid = str(row.get("user_id") or "").strip()
        role = str(row.get("role") or "").strip().lower()
        if not uid:
            continue
        role_map[uid] = role
        if role in {"gestor", "manager"}:
            gestor_uids.add(uid)

    scope_rows: list[dict] = []
    for table in ("tenant_user_departamentos", "tenant_user_scope"):
        try:
            rows = (
                svc.table(table)
                .select("user_id,departamento_id,grupo_id")
                .eq("tenant_id", tid)
                .execute()
                .data
            ) or []
            scope_rows.extend(rows)
        except Exception:
            pass

    if not scope_rows:
        return []

    if not gestor_uids:
        gestor_uids = {
            str(r.get("user_id") or "").strip()
            for r in scope_rows
            if str(r.get("user_id") or "").strip()
        }

    try:
        grupos_rows = (
            svc.table("equip_grupos")
            .select("id,nome,departamento_id")
            .eq("tenant_id", tid)
            .execute()
            .data
        ) or []
    except Exception:
        grupos_rows = []

    try:
        dep_rows = (
            svc.table("departamentos")
            .select("id,nome")
            .eq("tenant_id", tid)
            .execute()
            .data
        ) or []
    except Exception:
        dep_rows = []

    dep_nome_map = {str(r.get("id")): (r.get("nome") or "—") for r in dep_rows if r.get("id")}
    grupos_by_dep: dict[str, list[dict]] = defaultdict(list)
    grupo_nome_map: dict[str, str] = {}
    grupo_dep_map: dict[str, str] = {}
    for row in grupos_rows:
        gid = str(row.get("id") or "").strip()
        dep_id = str(row.get("departamento_id") or "").strip()
        if not gid:
            continue
        grupo_nome_map[gid] = row.get("nome") or gid
        grupo_dep_map[gid] = dep_id
        if dep_id:
            grupos_by_dep[dep_id].append(row)

    uid_dep_to_group_ids: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

    for row in scope_rows:
        uid = str(row.get("user_id") or "").strip()
        if not uid or uid not in gestor_uids:
            continue
        dep_id = str(row.get("departamento_id") or "").strip()
        gid = str(row.get("grupo_id") or "").strip()
        if gid and not dep_id:
            dep_id = grupo_dep_map.get(gid, "")
        if not dep_id:
            continue
        if gid:
            uid_dep_to_group_ids[uid][dep_id].add(gid)
        else:
            for grp in grupos_by_dep.get(dep_id, []):
                gid2 = str(grp.get("id") or "").strip()
                if gid2:
                    uid_dep_to_group_ids[uid][dep_id].add(gid2)

    if not uid_dep_to_group_ids:
        return []

    profile_map = _load_profiles_map(svc, sorted(uid_dep_to_group_ids.keys()))

    gestores: list[dict] = []
    for uid, dep_map in uid_dep_to_group_ids.items():
        grupos_flat: list[dict] = []
        seen: set[str] = set()
        for dep_id, gids in dep_map.items():
            for gid in sorted(gids):
                if gid in seen:
                    continue
                seen.add(gid)
                grupos_flat.append({
                    "grupo_id": gid,
                    "grupo_nome": grupo_nome_map.get(gid, gid),
                    "departamento_id": dep_id,
                    "departamento_nome": dep_nome_map.get(dep_id, "—"),
                    "label": f"{grupo_nome_map.get(gid, gid)} · {dep_nome_map.get(dep_id, '—')}",
                })

        if not grupos_flat:
            continue

        gestores.append({
            "gestor_id": uid,
            "gestor_nome": profile_map.get(uid) or f"Gestor {uid[:8]}",
            "email": "",
            "role": role_map.get(uid, ""),
            "departamentos": [
                {
                    "departamento_id": dep_id,
                    "departamento_nome": dep_nome_map.get(dep_id, "—"),
                }
                for dep_id in sorted(dep_map.keys())
            ],
            "grupos": sorted(grupos_flat, key=lambda x: (str(x.get("departamento_nome") or ""), str(x.get("grupo_nome") or ""))),
        })

    return sorted(gestores, key=lambda x: str(x.get("gestor_nome") or "").lower())


def _extract_semana_revisao(*dfs):
    """Retorna ultima semana encontrada + 1, usando a mesma heurística da Matriz."""
    for df in dfs:
        if not isinstance(df, pd.DataFrame) or df.empty:
            continue
        for col in df.columns:
            if "semana" not in str(col).lower():
                continue
            try:
                vals = df[col].astype(str).str.extract(r"(\d+)", expand=False)
                nums = pd.to_numeric(vals, errors="coerce").dropna()
                if not nums.empty:
                    return int(nums.max()) + 1
            except Exception:
                continue
    return None




def _load_payload_fresh_for_print(tid: str, gid: str, rid: str, lim: int, token: str = "") -> dict:
    """Carrega payload da matriz sem reaproveitar cache, para impressão.

    Evita reutilizar uma lista antiga de equipamentos no fluxo de impressão
    quando a composição do grupo mudou recentemente.
    """
    try:
        from src.ui.pages.matriz_modular.data import _fetch_template, _sb_from_token
    except Exception:
        return {"eqs": [], "s2s": {}, "all_s": [], "tarefas": []}

    _sb = _sb_from_token(token)
    try:
        _eqs = (
            _sb.table("equipamentos")
            .select("id,frota,modelo")
            .eq("tenant_id", tid)
            .eq("grupo_id", gid)
            .eq("ativo", True)
            .order("frota")
            .limit(int(lim))
            .execute()
            .data
        ) or []
    except Exception:
        _eqs = []

    if not _eqs:
        return {"eqs": [], "s2s": {}, "all_s": [], "tarefas": []}

    try:
        from src.utils.eq_oculto import get_ocultos
        _ocultos = get_ocultos(_sb, tid, rid)
        if _ocultos:
            _eqs = [e for e in _eqs if e.get("id") not in _ocultos]
    except Exception:
        pass

    if not _eqs:
        return {"eqs": [], "s2s": {}, "all_s": [], "tarefas": []}

    _s2s, _all_s = _fetch_template(_sb, tid, gid)
    if not _all_s:
        return {"eqs": _eqs, "s2s": {}, "all_s": [], "tarefas": []}

    try:
        _tarefas = (
            _sb.table("tarefas_servico")
            .select(
                "id,equipamento_id,servico_id,revisao_id,status,semana,observacao,"
                "etapa_d,etapa_r,etapa_m,dt_inicio,dt_etapa_d,dt_etapa_r,dt_etapa_m,updated_at"
            )
            .eq("tenant_id", tid)
            .eq("revisao_id", rid)
            .in_("equipamento_id", [e["id"] for e in _eqs])
            .execute()
            .data
        ) or []
    except Exception:
        _tarefas = []

    return {"eqs": _eqs, "s2s": _s2s, "all_s": _all_s, "tarefas": _tarefas}


def _build_group_pdf_same_as_matriz(
    tid: str,
    revisao_id: str,
    grupo_id: str,
    grupo_nome: str,
    revisao: dict,
    semana_impressao: int | None = None,
    token: str = "",
) -> bytes | None:
    """Gera exatamente o mesmo PDF da aba Matriz -> Exportações para um grupo."""
    try:
        from src.ui.pages.matriz_modular.data import _load_payload
        from src.ui.pages.matriz_modular.context import (
            _build_eq_labels,
            _build_resumo_df,
            _build_sector_tables_for_export,
            _build_view_agg,
        )
        from src.ui.pages.matriz_modular.pdf_export import _build_pdf_tables
    except Exception:
        return None

    payload = _load_payload_fresh_for_print(
        tid=tid,
        gid=grupo_id,
        rid=revisao_id,
        lim=10000,
        token=token,
    ) or {}

    eqs = payload.get("eqs") or []
    all_services = payload.get("all_s") or []
    setor_to_services = payload.get("s2s") or {}
    tarefas = payload.get("tarefas") or []
    if not eqs or not all_services or not setor_to_services:
        return None

    task_map = {
        (str(t.get("equipamento_id") or ""), str(t.get("servico_id") or "")): t
        for t in tarefas
        if t.get("equipamento_id") and t.get("servico_id")
    }
    eq_label, eq_label_short = _build_eq_labels(eqs, set())
    resumo_df, _, _, _ = _build_resumo_df(eqs, all_services, task_map, eq_label)
    sector_tables_for_export = _build_sector_tables_for_export(eqs, setor_to_services, task_map, eq_label_short)
    view_agg = _build_view_agg(eqs, all_services, task_map, eq_label)
    tarefas_df = pd.DataFrame(tarefas) if tarefas else pd.DataFrame()

    semana_revisao = _extract_semana_revisao(
        resumo_df,
        view_agg if isinstance(view_agg, pd.DataFrame) else pd.DataFrame(),
        pd.concat(
            [df for _, df in sector_tables_for_export if isinstance(df, pd.DataFrame)],
            ignore_index=True,
            sort=False,
        ) if sector_tables_for_export else pd.DataFrame(),
        tarefas_df,
    )

    return _build_pdf_tables(
        titulo=revisao.get("titulo") or "Revisão",
        grupo_nome=grupo_nome,
        resumo_df=resumo_df.copy() if isinstance(resumo_df, pd.DataFrame) else pd.DataFrame(),
        sector_tables=[(setor_nome, setor_df.copy()) for setor_nome, setor_df in (sector_tables_for_export or [])],
        semana_revisao=semana_revisao,
        tarefas_servico_df=tarefas_df.copy() if isinstance(tarefas_df, pd.DataFrame) else None,
        revisao_id=revisao_id,
        semana_impressa=semana_impressao,
    )


def _resolve_semana_impressao(semana_atual: int | None) -> int | None:
    try:
        semana = int(semana_atual or 0)
    except Exception:
        return None
    return semana + 1 if semana > 0 else None


def build_manager_print_documents(
    tid: str,
    revisao_id: str,
    selections: list[dict],
    revisao: dict,
    semana_atual: int,
    _token: str = "",
) -> list[dict]:
    """Gera os PDFs individuais para download/impressão por grupo selecionado."""
    docs: list[dict] = []
    seen: set[tuple[str, str]] = set()
    semana_impressao = _resolve_semana_impressao(semana_atual)

    for item in selections or []:
        gid = str(item.get("grupo_id") or "").strip()
        gestor_nome = str(item.get("gestor_nome") or "Gestor").strip() or "Gestor"
        grupo_nome = str(item.get("grupo_nome") or gid).strip() or gid
        if not gid:
            continue

        dedup_key = (gestor_nome, gid)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        pdf_bytes = _build_group_pdf_same_as_matriz(
            tid=tid,
            revisao_id=revisao_id,
            grupo_id=gid,
            grupo_nome=grupo_nome,
            revisao=revisao,
            semana_impressao=semana_impressao,
            token=_token,
        )
        if not pdf_bytes:
            continue

        fname = (
            f"Semana_{int(semana_impressao or semana_atual or 1):02d}__"
            f"{_slugify(gestor_nome)}__"
            f"{_slugify(grupo_nome)}.pdf"
        )
        docs.append({
            "gestor_id": str(item.get("gestor_id") or ""),
            "gestor_nome": gestor_nome,
            "grupo_id": gid,
            "grupo_nome": grupo_nome,
            "departamento_id": str(item.get("departamento_id") or ""),
            "departamento_nome": str(item.get("departamento_nome") or "—"),
            "file_name": fname,
            "pdf_bytes": pdf_bytes,
        })

    return docs


def build_manager_print_zip(
    tid: str,
    revisao_id: str,
    selections: list[dict],
    revisao: dict,
    semana_atual: int,
    _token: str = "",
) -> bytes:
    """Gera ZIP com 1 PDF por grupo, usando exatamente o layout da Matriz."""
    docs = build_manager_print_documents(
        tid=tid,
        revisao_id=revisao_id,
        selections=selections,
        revisao=revisao,
        semana_atual=semana_atual,
        _token=_token,
    )
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for doc in docs:
            zf.writestr(doc["file_name"], doc["pdf_bytes"])
    return mem.getvalue()


# Compatibilidade com versões anteriores
load_manager_print_targets = load_manager_print_options
