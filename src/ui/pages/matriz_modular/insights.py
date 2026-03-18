from __future__ import annotations

from datetime import datetime

from src.ui.pages.matriz_sector import summarize_sector_intelligence

def _fmt_duration_from_hours(hours) -> str:
    if hours is None:
        return "-"
    try:
        total_seconds = int(round(float(hours) * 3600))
    except Exception:
        return "-"
    if total_seconds < 0:
        total_seconds = 0
    days = total_seconds // 86400
    rem = total_seconds % 86400
    hrs = rem // 3600
    mins = (rem % 3600) // 60
    if days >= 1:
        return f"{days} dia{'s' if days != 1 else ''} e {hrs}h"
    if hrs >= 1:
        return f"{hrs} hora{'s' if hrs != 1 else ''}"
    return f"{mins} min"


def _sector_priority_sort_key(item: dict) -> tuple:
    risk_order = {"alto": 0, "medio": 1, "baixo": 2}
    return (
        risk_order.get(str(item.get("risk")), 3),
        -int(item.get("criticos", 0) or 0),
        -int(item.get("atrasadas_m", 0) or 0),
        int(item.get("pct", 0) or 0),
        str(item.get("setor_nome") or ""),
    )


def _build_group_sector_intelligence(
    *,
    equipamentos: list[dict],
    setor_to_services: dict,
    task_map: dict,
    atraso_dias: int,
    rev_start,
) -> list[dict]:
    intelligence: list[dict] = []
    for setor_nome in sorted(setor_to_services.keys(), key=lambda x: x.lower()):
        svs = sorted(
            setor_to_services[setor_nome],
            key=lambda x: (x.get("nome") or "").lower(),
        )
        svc_ids = [s.get("id") for s in svs if s.get("id")]
        if not svc_ids:
            continue
        intel = summarize_sector_intelligence(
            equipamentos=equipamentos,
            svc_ids=svc_ids,
            task_map=task_map,
            atraso_dias=int(atraso_dias),
            rev_start=rev_start,
        )
        intel["setor_nome"] = setor_nome
        intelligence.append(intel)
    return intelligence


def _build_automation_insights(
    *,
    sector_intelligence: list[dict],
    progresso_atual: float,
    meta_atual: float,
    critical_eq_count: int,
    no_start_eq_count: int,
) -> list[dict]:
    insights: list[dict] = []
    delta = round(float(progresso_atual) - float(meta_atual), 1)

    if delta <= -10:
        insights.append(
            {
                "nivel": "error",
                "titulo": "Ritmo abaixo da meta",
                "texto": f"O grupo está {abs(delta):.1f}% abaixo da meta linear da revisão.",
            }
        )
    elif delta < 0:
        insights.append(
            {
                "nivel": "warning",
                "titulo": "Leve atraso no ritmo",
                "texto": f"O grupo está {abs(delta):.1f}% abaixo da meta esperada.",
            }
        )
    else:
        insights.append(
            {
                "nivel": "success",
                "titulo": "Ritmo dentro da meta",
                "texto": f"O grupo está {delta:.1f}% acima da meta esperada.",
            }
        )

    delayed_mount = sum(int(item.get("atrasadas_m", 0) or 0) for item in sector_intelligence)
    if delayed_mount > 0:
        insights.append(
            {
                "nivel": "warning",
                "titulo": "Montagens atrasadas detectadas",
                "texto": f"Há {delayed_mount} montagem(ns) pendente(s) além do limite configurado.",
            }
        )

    high_risk = [item for item in sector_intelligence if item.get("risk") == "alto"]
    if high_risk:
        nomes = ", ".join(str(item.get("setor_nome")) for item in high_risk[:3])
        insights.append(
            {
                "nivel": "error",
                "titulo": f"{len(high_risk)} setor(es) em risco alto",
                "texto": f"Priorize: {nomes}" + ("..." if len(high_risk) > 3 else ""),
            }
        )

    if no_start_eq_count > 0:
        insights.append(
            {
                "nivel": "info",
                "titulo": "Frotas sem início",
                "texto": f"{no_start_eq_count} frota(s) ainda estão em 0% nesta revisão.",
            }
        )

    if critical_eq_count > 0:
        insights.append(
            {
                "nivel": "warning",
                "titulo": "Equipamentos críticos",
                "texto": f"{critical_eq_count} frota(s) estão abaixo de 50% de conclusão.",
            }
        )

    return insights


