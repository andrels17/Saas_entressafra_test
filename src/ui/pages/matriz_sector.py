
from __future__ import annotations

from typing import Any

import pandas as pd


def build_sector_frame(
    *,
    equipamentos: list[dict],
    svc_ids: list[str],
    svc_names: list[str],
    task_map: dict,
    eq_label_short: dict[str, str],
) -> tuple[pd.DataFrame, dict[str, tuple[str, str]], dict[str, str]]:
    rows: list[dict[str, Any]] = []
    col_meta: dict[str, tuple[str, str]] = {}
    obs_map: dict[str, str] = {}

    for e in equipamentos:
        total = max(len(svc_ids) * 3, 1)
        row = {"_equip_id": str(e["id"]), "%": 0, "Equipamento": eq_label_short[e["id"]]}
        done_c = 0

        for sid, sname in zip(svc_ids, svc_names):
            t = task_map.get((str(e["id"]), str(sid))) or {}
            d = bool(t.get("etapa_d"))
            r = bool(t.get("etapa_r"))
            m = bool(t.get("etapa_m"))

            cd = f"{sname} D"
            cr = f"{sname} R"
            cm = f"{sname} M"

            row[cd] = d
            row[cr] = r
            row[cm] = m

            col_meta.setdefault(cd, (sid, "etapa_d"))
            col_meta.setdefault(cr, (sid, "etapa_r"))
            col_meta.setdefault(cm, (sid, "etapa_m"))

            obs = (t.get("observacao") or "").strip()
            if obs:
                obs_map[f"{e['id']}__{sid}"] = obs

            done_c += int(d) + int(r) + int(m)

        row["%"] = round((done_c / total) * 100)
        rows.append(row)

    df = pd.DataFrame(rows)
    for sid, sname in zip(svc_ids, svc_names):
        for suffix in ("D", "R", "M"):
            col = f"{sname} {suffix}"
            if col in df.columns:
                df[col] = df[col].astype(object)
    return df, col_meta, obs_map


def sector_progress_label(*, equipamentos: list[dict], svc_ids: list[str], task_map: dict, setor_nome: str) -> tuple[int, int, int, str]:
    done_steps = 0
    total_steps = 0
    for e in equipamentos:
        for sid in svc_ids:
            t = task_map.get((str(e["id"]), str(sid))) or {}
            done_steps += int(bool(t.get("etapa_d"))) + int(bool(t.get("etapa_r"))) + int(bool(t.get("etapa_m")))
            total_steps += 3

    pct = round((done_steps / max(total_steps, 1)) * 100)
    icon = "🟢" if pct >= 80 else ("🟡" if pct >= 50 else "🔴")
    label = f"{icon} {setor_nome}  —  {pct}%  ({done_steps}/{total_steps})"
    return done_steps, total_steps, pct, label


def sector_summary_metrics(df_display: pd.DataFrame, svc_bool: list[str]) -> tuple[int, int, int, int]:
    tok_s = int(df_display[svc_bool].sum(numeric_only=True).sum()) if svc_bool else 0
    tc_s = int(len(df_display) * max(len(svc_bool), 1))
    pg = round((tok_s / max(tc_s, 1)) * 100)
    if svc_bool and not df_display.empty:
        pm = int(round(df_display.apply(
            lambda rw: (int(rw[svc_bool].sum()) / max(len(svc_bool), 1)) * 100,
            axis=1,
        ).mean()))
    else:
        pm = 0
    eq_100s = sum(1 for _, rw in df_display.iterrows() if int(rw.get("%", 0)) >= 100)
    return tok_s, tc_s, pg, pm, eq_100s


def build_change_preview_lines(
    changes: list[tuple],
    *,
    eq_label_short: dict[str, str],
    svc_names: dict[str, str],
    field_labels: dict[str, str],
    limit: int = 8,
) -> list[str]:
    preview: list[str] = []
    for eid, sid, field, nv in changes[:limit]:
        eq_name = eq_label_short.get(eid, str(eid))
        svc_name = svc_names.get(str(sid), str(sid))
        icon = "✅" if nv else "☐"
        preview.append(
            f"- Frota **{eq_name}** · {svc_name} · **{field_labels.get(field, field)}** → {icon}"
        )
    if len(changes) > limit:
        preview.append(f"- _...e mais {len(changes) - limit} alterações_")
    return preview


def summarize_sector_intelligence(
    *,
    equipamentos: list[dict],
    svc_ids: list[str],
    task_map: dict,
    atraso_dias: int,
    rev_start,
) -> dict[str, Any]:
    total_eq = len(equipamentos)
    total_services = len(svc_ids)
    total_steps = max(total_eq * total_services * 3, 1)
    done_steps = 0
    criticos = 0
    sem_inicio = 0
    em_andamento = 0
    atrasadas_m = 0

    days_since = 0
    try:
        if isinstance(rev_start, pd.Timestamp):
            days_since = int((pd.Timestamp.utcnow().tz_localize("UTC") - rev_start).days)
    except Exception:
        days_since = 0

    for e in equipamentos:
        eq_done = 0
        has_any = False
        has_partial = False
        has_missing_m = False

        for sid in svc_ids:
            t = task_map.get((str(e["id"]), str(sid))) or {}
            d = bool(t.get("etapa_d"))
            r = bool(t.get("etapa_r"))
            m = bool(t.get("etapa_m"))

            done_steps += int(d) + int(r) + int(m)
            eq_done += int(d) + int(r) + int(m)
            has_any = has_any or d or r or m
            has_partial = has_partial or ((d or r or m) and not (d and r and m))
            has_missing_m = has_missing_m or (not m)

            if days_since > atraso_dias and not m:
                atrasadas_m += 1

        if eq_done == 0:
            sem_inicio += 1
        elif has_partial:
            em_andamento += 1

        eq_total = max(len(svc_ids) * 3, 1)
        eq_pct = round((eq_done / eq_total) * 100)
        if eq_pct < 50:
            criticos += 1

    pct = round((done_steps / total_steps) * 100)
    if pct < 50 or criticos >= max(1, total_eq // 3):
        risk = "alto"
        risk_label = "ALTO"
        risk_icon = "🔴"
    elif pct < 80 or atrasadas_m > 0:
        risk = "medio"
        risk_label = "MÉDIO"
        risk_icon = "🟡"
    else:
        risk = "baixo"
        risk_label = "BAIXO"
        risk_icon = "🟢"

    if sem_inicio >= max(2, total_eq // 2):
        recommendation = "Iniciar as frotas sem progresso primeiro."
    elif atrasadas_m > 0:
        recommendation = "Priorizar etapas de montagem pendentes."
    elif criticos > 0:
        recommendation = "Atacar as frotas abaixo de 50%."
    else:
        recommendation = "Setor sob controle; manter o ritmo atual."

    return {
        "pct": pct,
        "risk": risk,
        "risk_label": risk_label,
        "risk_icon": risk_icon,
        "criticos": int(criticos),
        "sem_inicio": int(sem_inicio),
        "em_andamento": int(em_andamento),
        "atrasadas_m": int(atrasadas_m),
        "recommendation": recommendation,
        "total_eq": int(total_eq),
        "total_services": int(total_services),
        "done_steps": int(done_steps),
        "total_steps": int(total_steps),
    }
