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


@st.cache_data(ttl=60, show_spinner=False)
def load_manager_print_options(tid: str, ver: str = "0", _token: str = "") -> list[dict]:
    """Retorna gestores e grupos disponíveis para geração do ZIP de impressão."""
    try:
        from src.services.email.recipients import get_manager_pdf_bundles
        bundles = get_manager_pdf_bundles(tid) or []
    except Exception:
        bundles = []
    if not bundles:
        return []

    sb = get_supabase_anon()
    if _token:
        sb.postgrest.auth(_token)

    try:
        grupos_rows = (
            sb.table("equip_grupos")
            .select("id,nome,departamento_id")
            .eq("tenant_id", tid)
            .order("nome")
            .execute()
            .data
        ) or []
    except Exception:
        grupos_rows = []

    try:
        dep_rows = (
            sb.table("departamentos")
            .select("id,nome")
            .eq("tenant_id", tid)
            .execute()
            .data
        ) or []
    except Exception:
        dep_rows = []

    dep_nome_map = {str(r.get("id")): r.get("nome") or "—" for r in dep_rows if r.get("id")}
    grupo_map = {str(r.get("id")): r for r in grupos_rows if r.get("id")}

    gestores: list[dict] = []
    for bundle in bundles:
        recipient = bundle.recipient
        grupos_flat: list[dict] = []
        seen: set[str] = set()

        for dep_id, dep_nome in zip(bundle.departamento_ids, bundle.departamento_nomes):
            dep_id = str(dep_id)
            explicit_gids = [str(g) for g in (bundle.grupo_ids_por_dept.get(dep_id) or [])]
            for gid in explicit_gids:
                row = grupo_map.get(gid, {})
                if gid in seen:
                    continue
                seen.add(gid)
                grupos_flat.append({
                    "grupo_id": gid,
                    "grupo_nome": row.get("nome") or gid,
                    "departamento_id": dep_id,
                    "departamento_nome": dep_nome or dep_nome_map.get(dep_id, "—"),
                    "label": f"{row.get('nome') or gid} · {dep_nome or dep_nome_map.get(dep_id, '—')}",
                })

        if not grupos_flat:
            continue

        gestores.append({
            "gestor_id": str(recipient.user_id),
            "gestor_nome": recipient.nome or recipient.email or "Gestor",
            "email": recipient.email or "",
            "departamentos": [
                {
                    "departamento_id": str(dep_id),
                    "departamento_nome": dep_nome or dep_nome_map.get(str(dep_id), "—"),
                }
                for dep_id, dep_nome in zip(bundle.departamento_ids, bundle.departamento_nomes)
            ],
            "grupos": sorted(grupos_flat, key=lambda x: (str(x["departamento_nome"]), str(x["grupo_nome"]))),
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
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.enums import TA_LEFT

    buff = io.BytesIO()
    doc = SimpleDocTemplate(buff, pagesize=landscape(A4), leftMargin=0.8*cm, rightMargin=0.8*cm, topMargin=0.8*cm, bottomMargin=0.8*cm)
    styles = getSampleStyleSheet()
    title = ParagraphStyle("title", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=14, leading=16, alignment=TA_LEFT, textColor=colors.HexColor("#111827"))
    meta = ParagraphStyle("meta", parent=styles["BodyText"], fontSize=8.5, leading=10, textColor=colors.HexColor("#374151"))
    small = ParagraphStyle("small", parent=styles["BodyText"], fontSize=7.4, leading=8.5, textColor=colors.HexColor("#111827"))

    story = [
        Paragraph(f"Matriz para impressão — {grupo_nome}", title),
        Spacer(1, 0.15*cm),
        Paragraph(f"Revisão: <b>{revisao_titulo or '—'}</b> · Semana: <b>{semana_atual}</b>", meta),
        Paragraph(f"Gestor: <b>{gestor_nome or '—'}</b> · Departamento: <b>{departamento_nome or '—'}</b>", meta),
        Spacer(1, 0.35*cm),
    ]

    table_data = [[Paragraph(f"<b>{c}</b>", small) for c in df.columns]]
    for _, row in df.iterrows():
        table_data.append([Paragraph(str(row.get(c, "") or "—"), small) for c in df.columns])

    widths = [2.2*cm, 3.1*cm, 2.7*cm, 5.2*cm, 0.8*cm, 0.8*cm, 0.8*cm, 2.3*cm, 5.6*cm]
    tbl = Table(table_data, colWidths=widths, repeatRows=1)
    style_cmds = [
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#0F172A")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("LEFTPADDING", (0,0), (-1,-1), 4),
        ("RIGHTPADDING", (0,0), (-1,-1), 4),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ]
    for i in range(1, len(table_data)):
        bg = colors.HexColor("#F8FAFC") if i % 2 == 0 else colors.white
        style_cmds.append(("BACKGROUND", (0, i), (-1, i), bg))
    tbl.setStyle(TableStyle(style_cmds))
    story.append(tbl)
    doc.build(story)
    return buff.getvalue()


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
