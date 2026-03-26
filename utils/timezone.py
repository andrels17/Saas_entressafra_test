"""Utilitários de data/hora com fuso horário correto (Brasília = UTC-3).

Regra única do projeto:
  - NUNCA use datetime.now() sem fuso — reflete o servidor (provavelmente UTC).
  - NUNCA use datetime.utcnow() — depreciado e produz datetime naive.
  - USE now_brt() para exibição ao usuário (PDFs, e-mails, logs BR).
  - USE now_utc() para gravar no banco (Supabase espera UTC com offset).

Exemplos:
    from src.utils.timezone import now_brt, now_utc, fmt_brt, parse_utc_to_brt

    # Exibição em PDF / e-mail
    stamp = fmt_brt()              # "13/03/2026 06:00"
    stamp = fmt_brt("%d/%m/%Y")    # "13/03/2026"

    # Gravar no banco
    payload["updated_at"] = now_utc_iso()  # "2026-03-13T09:00:00+00:00"

    # Converter timestamp do banco para BRT antes de exibir
    dt_brt = parse_utc_to_brt("2026-03-13T09:00:00+00:00")
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

# Brasília = UTC-3 (sem DST)
BRT = timezone(timedelta(hours=-3))
UTC = timezone.utc


def now_brt() -> datetime:
    """Retorna datetime atual em horário de Brasília (aware)."""
    return datetime.now(BRT)


def now_utc() -> datetime:
    """Retorna datetime atual em UTC (aware)."""
    return datetime.now(UTC)


def now_utc_iso() -> str:
    """ISO 8601 UTC com offset, pronto para gravar no Supabase.

    Exemplo: '2026-03-13T09:00:00+00:00'
    """
    return now_utc().isoformat(timespec="seconds")


def fmt_brt(fmt: str = "%d/%m/%Y %H:%M") -> str:
    """Formata o instante atual em BRT para exibição ao usuário.

    Uso típico em PDFs e e-mails onde o usuário espera horário de Brasília.
    """
    return now_brt().strftime(fmt)


def parse_utc_to_brt(ts_str: str | None) -> datetime | None:
    """Converte uma string ISO UTC (do banco) para datetime em BRT.

    Aceita formatos: '2026-03-13T09:00:00+00:00', '2026-03-13T09:00:00Z',
                     '2026-03-13T09:00:00' (assume UTC se naive).
    Retorna None se ts_str for None ou inválido.
    """
    if not ts_str:
        return None
    try:
        s = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(BRT)
    except Exception:
        return None


def days_since_utc(ts_str: str | None) -> int | None:
    """Retorna quantos dias completos se passaram desde um timestamp UTC.

    Usa UTC para cálculo de diferença (independente de fuso).
    Retorna None se ts_str for None ou inválido.
    """
    if not ts_str:
        return None
    try:
        s = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return int((now_utc() - dt).total_seconds() // 86400)
    except Exception:
        return None


def semana_da_revisao(data_inicio_str: str | None, semanas_total: int) -> int:
    """Calcula a semana operacional atual de uma revisão.

    Usa BRT para alinhar com o calendário do usuário — evita que a semana
    vire à meia-noite UTC (03:00 BRT) em vez de à meia-noite BRT.

    Args:
        data_inicio_str: data de início da revisão (ISO, ex: '2026-01-06').
                         Interpretada como 00:00 BRT se vier sem fuso.
        semanas_total:   duração total da revisão em semanas.

    Returns:
        Número da semana atual (mínimo 1, máximo semanas_total).
    """
    if not data_inicio_str:
        return 1
    try:
        s = data_inicio_str.strip()
        # Suporta '2026-01-06', '2026-01-06T00:00:00', '2026-01-06T00:00:00Z'
        s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            # Data sem fuso: interpreta como meia-noite BRT (horário do
            # usuário)
            dt = dt.replace(tzinfo=BRT)
        inicio_brt = dt.astimezone(BRT)
        agora_brt = now_brt()
        days_elapsed = (agora_brt - inicio_brt).days
        semana = max(1, days_elapsed // 7 + 1)
        return min(semana, semanas_total or semana)
    except Exception:
        return 1
