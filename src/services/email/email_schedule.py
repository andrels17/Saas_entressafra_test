"""Gerenciamento da configuração de agendamento de e-mail.

Persiste em `email_schedule_config` no Supabase.
Lida com leitura, escrita e cálculo do próximo disparo.

Esquema da tabela (ver sql/migration_email_schedule.sql):
  id            uuid PK
  tenant_id     uuid FK tenants
  ativo         boolean default true
  periodicidade text  'semanal' | 'quinzenal' | 'mensal'
  dia_semana    int   0=Dom … 6=Sab (usado em semanal/quinzenal)
  hora_envio    text  HH:MM (horário de Brasília)
  dias_travado  int   default 2
  dias_parado   int   default 5
  revisao_fixa  uuid  null = sempre usa a revisão ativa
  updated_at    timestamptz
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

DIAS_SEMANA_LABELS = [
    "Domingo", "Segunda-feira", "Terça-feira", "Quarta-feira",
    "Quinta-feira", "Sexta-feira", "Sábado",
]

PERIODICIDADE_OPTS = ["semanal", "quinzenal", "mensal"]
PERIODICIDADE_LABELS = {
    "semanal":   "Semanal",
    "quinzenal": "Quinzenal (a cada 2 semanas)",
    "mensal":    "Mensal (mesmo dia todo mês)",
}

# Brasília = UTC-3
BRT = timezone(timedelta(hours=-3))


@dataclass
class ScheduleConfig:
    tenant_id: str
    ativo: bool = True
    periodicidade: str = "semanal"       # semanal | quinzenal | mensal
    dia_semana: int = 1                  # 0=Dom … 6=Sab (Monday=1)
    dia_mes: int = 1                     # 1-28, usado em 'mensal'
    hora_envio: str = "07:00"            # HH:MM BRT
    dias_travado: int = 2
    dias_parado: int = 5
    revisao_fixa: str | None = None
    id: str | None = None

    @property
    def hora_int(self) -> tuple[int, int]:
        try:
            h, m = self.hora_envio.split(":")
            return int(h), int(m)
        except Exception:
            return 7, 0

    def proximo_disparo_brt(self) -> datetime:
        """Calcula a próxima data/hora de disparo em horário de Brasília."""
        now = datetime.now(BRT)
        hh, mm = self.hora_int

        if self.periodicidade == "mensal":
            # mesmo dia do mês
            day = max(1, min(28, self.dia_mes))
            candidate = now.replace(day=day, hour=hh, minute=mm, second=0, microsecond=0)
            if candidate <= now:
                # próximo mês
                if candidate.month == 12:
                    candidate = candidate.replace(year=candidate.year + 1, month=1)
                else:
                    candidate = candidate.replace(month=candidate.month + 1)
            return candidate

        # semanal ou quinzenal — calcula próxima ocorrência do dia da semana
        # Python weekday(): Mon=0 … Sun=6; nossa convenção: 0=Dom … 6=Sab
        # Converte: dom=0→6, seg=1→0, ter=2→1 …
        target_py = (self.dia_semana - 1) % 7   # 0=Dom→6, 1=Seg→0 …
        days_ahead = (target_py - now.weekday()) % 7
        if days_ahead == 0:
            candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if candidate <= now:
                days_ahead = 7
        if days_ahead > 0:
            candidate = (now + timedelta(days=days_ahead)).replace(
                hour=hh, minute=mm, second=0, microsecond=0)

        if self.periodicidade == "quinzenal":
            # verifica se o último disparo foi há menos de 14 dias
            # (simplificação: sempre avança mais 7 dias se semana par/ímpar não coincide)
            # sem histórico real, retornamos apenas o próximo dia da semana
            pass

        return candidate

    def descricao_humana(self) -> str:
        if self.periodicidade == "mensal":
            return f"Mensal — todo dia {self.dia_mes} às {self.hora_envio} (Brasília)"
        dia = DIAS_SEMANA_LABELS[self.dia_semana % 7]
        per = "Semanal" if self.periodicidade == "semanal" else "Quinzenal"
        return f"{per} — toda {dia} às {self.hora_envio} (Brasília)"


# ── CRUD via Supabase ─────────────────────────────────────────────────────────

def _row_to_config(row: dict) -> ScheduleConfig:
    return ScheduleConfig(
        id=row.get("id"),
        tenant_id=row.get("tenant_id", ""),
        ativo=bool(row.get("ativo", True)),
        periodicidade=row.get("periodicidade") or "semanal",
        dia_semana=int(row.get("dia_semana") or 1),
        dia_mes=int(row.get("dia_mes") or 1),
        hora_envio=row.get("hora_envio") or "07:00",
        dias_travado=int(row.get("dias_travado") or 2),
        dias_parado=int(row.get("dias_parado") or 5),
        revisao_fixa=row.get("revisao_fixa"),
    )


def load_schedule_config(tenant_id: str) -> ScheduleConfig:
    """Carrega configuração do Supabase. Retorna default se não existir."""
    from src.db.supabase_client import get_supabase_service
    sb = get_supabase_service()
    try:
        rows = (
            sb.table("email_schedule_config")
            .select("*")
            .eq("tenant_id", tenant_id)
            .limit(1)
            .execute()
            .data
        )
        if rows:
            return _row_to_config(rows[0])
    except Exception:
        pass
    return ScheduleConfig(tenant_id=tenant_id)


def save_schedule_config(cfg: ScheduleConfig) -> bool:
    """Salva (upsert) a configuração. Retorna True se ok."""
    from src.db.supabase_client import get_supabase_service
    sb = get_supabase_service()
    payload: dict[str, Any] = {
        "tenant_id":    cfg.tenant_id,
        "ativo":        cfg.ativo,
        "periodicidade":cfg.periodicidade,
        "dia_semana":   cfg.dia_semana,
        "dia_mes":      cfg.dia_mes,
        "hora_envio":   cfg.hora_envio,
        "dias_travado": cfg.dias_travado,
        "dias_parado":  cfg.dias_parado,
        "revisao_fixa": cfg.revisao_fixa,
        "updated_at":   datetime.utcnow().isoformat(),
    }
    try:
        if cfg.id:
            sb.table("email_schedule_config").update(payload).eq("id", cfg.id).execute()
        else:
            sb.table("email_schedule_config").insert(payload).execute()
        return True
    except Exception as e:
        return False


def should_dispatch_now(cfg: ScheduleConfig, tolerance_minutes: int = 10) -> bool:
    """Verifica se o agendamento deve disparar agora (±tolerance_minutes)."""
    if not cfg.ativo:
        return False
    now = datetime.now(BRT)
    hh, mm = cfg.hora_int
    target_weekday_py = (cfg.dia_semana - 1) % 7   # conv. nossa → Python

    if cfg.periodicidade == "mensal":
        if now.day != max(1, min(28, cfg.dia_mes)):
            return False
    else:
        if now.weekday() != target_weekday_py:
            return False

    # Verifica faixa de horário
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    diff_min = abs((now - target).total_seconds() / 60)
    return diff_min <= tolerance_minutes
