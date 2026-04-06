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


def _load_group_tasks(tid: str, revisao_id: str, grupo_id: str, token: str = "") -> list[dict]:
    sb = get_supabase_anon()
    if token:
        sb.postgrest.auth(token)
    try:
        return (
            sb.table("tarefas_servico")
            .select(
                "id,status,observacao,semana,etapa_d,etapa_r,etapa_m,updated_at,"
                "servicos(id,nome,setor_id,setores(nome)),"
                "equipamentos(id,frota,modelo,grupo_id,equip_grupos(id,nome,departamento_id))"
            )
            .eq("tenant_id", tid)
            .eq("revisao_id", revisao_id)
            .eq("equipamentos.grupo_id", grupo_id)
            .order("updated_at", desc=True)
            .execute()
            .data
        ) or []
    except Exception:
        return []


def _group_rows_to_dataframe(rows: list[dict]) -> pd.DataFrame:
    items = []
    for t in rows or []:
        eq = t.get("equipamentos") or {}
        svc = t.get("servicos") or {}
        setor = (svc.get("setores") or {}).get("nome") or "—"
        items.append({
            "Equipamento": eq.get("frota") or eq.get("id") or "—",
            "Modelo": eq.get("modelo") or "—",
            "Setor": setor,
            "Serviço": svc.get("nome") or "—",
            "D": "OK" if t.get("etapa_d") else "",
            "R": "OK" if t.get("etapa_r") else "",
            "M": "OK" if t.get("etapa_m") else "",
            "Status": t.get("status") or "pendente",
            "Obs.": t.get("observacao") or "",
        })
    if not items:
        return pd.DataFrame(columns=["Equipamento", "Modelo", "Setor", "Serviço", "D", "R", "M", "Status", "Obs."])
    df = pd.DataFrame(items)
    return df.sort_values(["Setor", "Equipamento", "Serviço"], kind="stable").reset_index(drop=True)


def _build_group_pdf(*, revisao_titulo: str, semana_atual: int, gestor_nome: str, grupo_nome: str, departamento_nome: str, df: pd.DataFrame) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    mem = io.BytesIO()
    doc = SimpleDocTemplate(
        mem,
        pagesize=landscape(A4),
        leftMargin=0.7 * cm,
        rightMargin=0.7 * cm,
        topMargin=0.8 * cm,
        bottomMargin=0.8 * cm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ntf_zip_title",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=16,
        textColor=colors.HexColor("#111827"),
        spaceAfter=4,
    )
    meta_style = ParagraphStyle(
        "ntf_zip_meta",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#475569"),
        spaceAfter=2,
    )

    cols = [c for c in ["Equipamento", "Modelo", "Setor", "Serviço", "D", "R", "M", "Status", "Obs."] if c in df.columns]
    data_tbl = [cols] + df[cols].fillna("").astype(str).values.tolist()
    usable = landscape(A4)[0] - (1.4 * cm)
    widths = {
        "Equipamento": 2.5 * cm,
        "Modelo": 3.2 * cm,
        "Setor": 3.0 * cm,
        "Serviço": 7.0 * cm,
        "D": 0.9 * cm,
        "R": 0.9 * cm,
        "M": 0.9 * cm,
        "Status": 2.0 * cm,
        "Obs.": 6.0 * cm,
    }
    col_widths = [widths.get(c, usable / max(len(cols), 1)) for c in cols]

    tbl = Table(data_tbl, repeatRows=1, colWidths=col_widths)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("LEADING", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CBD5E1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (4, 1), (6, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))

    story = [
        Paragraph(f"Matriz para impressão · {grupo_nome}", title_style),
        Paragraph(f"Revisão: <b>{revisao_titulo}</b>", meta_style),
        Paragraph(f"Semana: <b>{int(semana_atual or 1)}</b> · Departamento: <b>{departamento_nome}</b> · Gestor: <b>{gestor_nome}</b>", meta_style),
        Spacer(1, 0.25 * cm),
        tbl,
    ]
    doc.build(story)
    return mem.getvalue()


def build_manager_print_zip(
    tid: str,
    revisao_id: str,
    selections: list[dict],
    revisao: dict,
    semana_atual: int,
    _token: str = "",
) -> bytes:
    """Gera ZIP com um PDF por grupo selecionado."""
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in selections or []:
            gid = str(item.get("grupo_id") or "")
            if not gid:
                continue
            rows = _load_group_tasks(tid, revisao_id, gid, _token)
            df = _group_rows_to_dataframe(rows)
            if df.empty:
                continue
            pdf_bytes = _build_group_pdf(
                revisao_titulo=revisao.get("titulo") or "Revisão",
                semana_atual=int(semana_atual or 1),
                gestor_nome=item.get("gestor_nome") or "Gestor",
                grupo_nome=item.get("grupo_nome") or gid,
                departamento_nome=item.get("departamento_nome") or "—",
                df=df,
            )
            fname = (
                f"Semana_{int(semana_atual or 1):02d}__"
                f"{_slugify(item.get('gestor_nome') or 'Gestor')}__"
                f"{_slugify(item.get('grupo_nome') or gid)}.pdf"
            )
            zf.writestr(fname, pdf_bytes)
    return mem.getvalue()
