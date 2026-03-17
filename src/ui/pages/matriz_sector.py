
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
        row = {"_equip_id": e["id"], "%": 0, "Equipamento": eq_label_short[e["id"]]}
        done_c = 0

        for sid, sname in zip(svc_ids, svc_names):
            t = task_map.get((e["id"], sid)) or {}
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

    return pd.DataFrame(rows), col_meta, obs_map


def sector_progress_label(*, equipamentos: list[dict], svc_ids: list[str], task_map: dict, setor_nome: str) -> tuple[int, int, int, str]:
    done_steps = 0
    total_steps = 0
    for e in equipamentos:
        for sid in svc_ids:
            t = task_map.get((e["id"], sid)) or {}
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



def build_mass_toggle_changes(
    frame: pd.DataFrame,
    *,
    svc_bool: list[str],
    col_meta: dict[str, tuple[str, str]],
    target_field: str | None = None,
    target_value: bool = True,
) -> list[tuple]:
    """Gera alterações em massa a partir do estado atual da grade."""
    changes: list[tuple] = []
    if frame is None or frame.empty:
        return changes

    allowed_fields = {target_field} if target_field else {"etapa_d", "etapa_r", "etapa_m"}
    for equip_id, row in frame.iterrows():
        for col in svc_bool:
            meta = col_meta.get(col)
            if not meta:
                continue
            sid, field = meta
            if field not in allowed_fields:
                continue
            current = bool(row.get(col, False))
            if current != bool(target_value):
                changes.append((equip_id, sid, field, bool(target_value)))
    return changes


def summarize_change_payload(
    changes: list[tuple],
    *,
    field_labels: dict[str, str],
    semana: int | None = None,
) -> list[str]:
    if not changes:
        return []

    total = len(changes)
    checked = sum(1 for _, _, _, nv in changes if bool(nv))
    unchecked = total - checked
    per_field: dict[str, int] = {}
    for _, _, field, _ in changes:
        per_field[field] = per_field.get(field, 0) + 1

    parts = [f"**{total} alteração(ões)**"]
    if checked:
        parts.append(f"{checked} marcação(ões)")
    if unchecked:
        parts.append(f"{unchecked} desmarcação(ões)")
    lines = [" · ".join(parts)]

    detail = " · ".join(
        f"{field_labels.get(field, field)}: {qty}"
        for field, qty in sorted(per_field.items(), key=lambda item: field_labels.get(item[0], item[0]))
    )
    if detail:
        lines.append(detail)
    if semana is not None:
        lines.append(f"Semana aplicada em tarefas sem semana definida: **{int(semana)}**")
    return lines



def build_sector_intelligence(
    *,
    equipamentos: list[dict],
    svc_ids: list[str],
    task_map: dict,
    setor_nome: str,
    atraso_dias: int = 7,
    days_since_start: int = 0,
) -> dict[str, Any]:
    eq_total = len(equipamentos)
    total_steps = max(len(svc_ids) * 3, 1)
    pct_values: list[int] = []
    eq_critical = 0
    eq_not_started = 0
    eq_done_full = 0
    atraso_m = 0
    done_steps = 0

    for e in equipamentos:
        done_eq = 0
        missing_mount_eq = 0
        for sid in svc_ids:
            t = task_map.get((e["id"], sid)) or {}
            d = bool(t.get("etapa_d"))
            r = bool(t.get("etapa_r"))
            m = bool(t.get("etapa_m"))
            done_eq += int(d) + int(r) + int(m)
            if not m:
                missing_mount_eq += 1
        pct_eq = round((done_eq / total_steps) * 100)
        pct_values.append(pct_eq)
        done_steps += done_eq
        eq_critical += int(pct_eq < 50)
        eq_not_started += int(pct_eq == 0)
        eq_done_full += int(pct_eq >= 100)
        if days_since_start > atraso_dias and missing_mount_eq > 0:
            atraso_m += 1

    pct_setor = round((done_steps / max(eq_total * len(svc_ids) * 3, 1)) * 100)
    avg_pct = round(sum(pct_values) / max(len(pct_values), 1)) if pct_values else 0
    risk_score = (eq_critical * 3) + (eq_not_started * 2) + atraso_m + max(0, 70 - pct_setor) // 10
    if pct_setor >= 85 and eq_critical == 0:
        risk_level = "baixo"
        risk_icon = "🟢"
    elif pct_setor >= 60 and eq_critical <= max(1, eq_total // 4):
        risk_level = "moderado"
        risk_icon = "🟡"
    else:
        risk_level = "alto"
        risk_icon = "🔴"

    recommendation = "Manter ritmo atual."
    if atraso_m:
        recommendation = "Priorize montagens pendentes deste setor."
    elif eq_not_started >= max(2, eq_total // 3 if eq_total else 2):
        recommendation = "Há muitas frotas sem início; vale redistribuir carga."
    elif eq_critical:
        recommendation = "Revisar frotas críticas antes do próximo lote."

    return {
        "setor": setor_nome,
        "pct": pct_setor,
        "pct_medio": avg_pct,
        "eq_total": eq_total,
        "eq_critical": eq_critical,
        "eq_not_started": eq_not_started,
        "eq_done_full": eq_done_full,
        "atraso_m": atraso_m,
        "risk_score": int(risk_score),
        "risk_level": risk_level,
        "risk_icon": risk_icon,
        "recommendation": recommendation,
    }


def build_global_intelligence_summary(sector_infos: list[dict[str, Any]]) -> dict[str, Any]:
    if not sector_infos:
        return {
            "critical_sectors": 0,
            "critical_equipment": 0,
            "delayed_sectors": 0,
            "top_risks": [],
            "alerts": [],
        }

    ordered = sorted(
        sector_infos,
        key=lambda item: (item.get("risk_score", 0), item.get("eq_critical", 0), -item.get("pct", 0)),
        reverse=True,
    )
    critical_sectors = sum(1 for item in sector_infos if item.get("risk_level") == "alto")
    critical_equipment = sum(int(item.get("eq_critical") or 0) for item in sector_infos)
    delayed_sectors = sum(1 for item in sector_infos if int(item.get("atraso_m") or 0) > 0)

    alerts: list[str] = []
    if critical_sectors:
        alerts.append(f"{critical_sectors} setor(es) em risco alto neste grupo.")
    if critical_equipment:
        alerts.append(f"{critical_equipment} frota(s) crítica(s) abaixo de 50%.")
    if delayed_sectors:
        alerts.append(f"{delayed_sectors} setor(es) com montagem pendente além do limite de atraso.")
    if not alerts:
        alerts.append("Nenhum alerta crítico no momento.")

    return {
        "critical_sectors": critical_sectors,
        "critical_equipment": critical_equipment,
        "delayed_sectors": delayed_sectors,
        "top_risks": ordered[:3],
        "alerts": alerts,
    }
