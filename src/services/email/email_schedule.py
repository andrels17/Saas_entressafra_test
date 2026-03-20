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

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from src.utils.timezone import BRT, now_utc_iso

log = logging.getLogger("saas.email_schedule")

DIAS_SEMANA_LABELS = [
    "Domingo", "Segunda-feira", "Terça-feira", "Quarta-feira",
    "Quinta-feira", "Sexta-feira", "Sábado",
]

PERIODICIDADE_OPTS = ["semanal", "quinzenal", "mensal"]
PERIODICIDADE_LABELS = {
    "semanal": "Semanal",
    "quinzenal": "Quinzenal (a cada 2 semanas)",
    "mensal": "Mensal (mesmo dia todo mês)",
}


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
    last_dispatched_at: str | None = None  # ISO UTC — controla duplo disparo

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
            candidate = now.replace(
                day=day, hour=hh, minute=mm, second=0, microsecond=0)
            if candidate <= now:
                # próximo mês
                if candidate.month == 12:
                    candidate = candidate.replace(
                        year=candidate.year + 1, month=1)
                else:
                    candidate = candidate.replace(month=candidate.month + 1)
            return candidate

        # semanal ou quinzenal — calcula próxima ocorrência do dia da semana
        # Python weekday(): Mon=0 … Sun=6; nossa convenção: 0=Dom … 6=Sab
        # Converte: dom=0→6, seg=1→0, ter=2→1 …
        target_py = (self.dia_semana - 1) % 7   # 0=Dom→6, 1=Seg→0 …
        days_ahead = (target_py - now.weekday()) % 7
        if days_ahead == 0:
            candidate = now.replace(
                hour=hh, minute=mm, second=0, microsecond=0)
            if candidate <= now:
                days_ahead = 7
        if days_ahead > 0:
            candidate = (now + timedelta(days=days_ahead)).replace(
                hour=hh, minute=mm, second=0, microsecond=0)

        if self.periodicidade == "quinzenal":
            # Avança mais 7 dias se o último disparo foi há menos de 14 dias
            if self.last_dispatched_at:
                try:
                    last = datetime.fromisoformat(
                        self.last_dispatched_at.replace("Z", "+00:00")
                    ).astimezone(BRT)
                    days_since = (candidate - last).days
                    if days_since < 14:
                        candidate += timedelta(weeks=1)
                except Exception:
                    pass  # ignorado — operação opcional

        return candidate

    def descricao_humana(self) -> str:
        if self.periodicidade == "mensal":
            return f"Mensal — todo dia {
                self.dia_mes} às {
                self.hora_envio} (Brasília)"
        dia = DIAS_SEMANA_LABELS[self.dia_semana % 7]
        per = "Semanal" if self.periodicidade == "semanal" else "Quinzenal"
        return f"{per} — toda {dia} às {self.hora_envio} (Brasília)"


# ── CRUD via Supabase ───────────────────────────────────────────────────

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
        last_dispatched_at=row.get("last_dispatched_at"),
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
    except Exception as exc:
        # Scheduler continuará com configuração default — logar para diagnóstico
        # (sem este log, disparos em horário errado são impossíveis de depurar)
        log.warning(
            "load_schedule_config: falha ao carregar config do tenant %s, "
            "usando defaults: %s",
            tenant_id, exc,
        )
    return ScheduleConfig(tenant_id=tenant_id)


def save_schedule_config(cfg: ScheduleConfig) -> bool:
    """Salva (upsert) a configuração. Retorna True se ok."""
    from src.db.supabase_client import get_supabase_service
    sb = get_supabase_service()
    payload: dict[str, Any] = {
        "tenant_id": cfg.tenant_id,
        "ativo": cfg.ativo,
        "periodicidade": cfg.periodicidade,
        "dia_semana": cfg.dia_semana,
        "dia_mes": cfg.dia_mes,
        "hora_envio": cfg.hora_envio,
        "dias_travado": cfg.dias_travado,
        "dias_parado": cfg.dias_parado,
        "revisao_fixa": cfg.revisao_fixa,
        "updated_at": now_utc_iso(),
    }
    try:
        if cfg.id:
            sb.table("email_schedule_config").update(
                payload).eq("id", cfg.id).execute()
        else:
            sb.table("email_schedule_config").insert(payload).execute()
        return True
    except Exception as exc:
        log.error(
            "save_schedule_config: falha ao salvar agendamento do tenant %s: %s",
            cfg.tenant_id, exc,
        )
        return False


def should_dispatch_now(
        cfg: ScheduleConfig,
        tolerance_minutes: int = 10) -> bool:
    """Verifica se o agendamento deve disparar agora (±tolerance_minutes).

    Proteção anti-duplo-disparo: se `last_dispatched_at` estiver preenchido
    e o disparo tiver ocorrido dentro da janela de periodicidade mínima
    (24h para semanal/mensal, 14 dias para quinzenal), retorna False mesmo
    que o horário bata. Isso evita múltiplos disparos quando o scheduler
    roda a cada minuto dentro da janela de tolerância.
    """
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
    if diff_min > tolerance_minutes:
        return False

    # ── Proteção anti-duplo-disparo ─────────────────────────────────────────
    if cfg.last_dispatched_at:
        try:
            last = datetime.fromisoformat(
                cfg.last_dispatched_at.replace("Z", "+00:00")
            ).astimezone(BRT)
            hours_since = (now - last).total_seconds() / 3600
            # Mínimo de horas entre disparos por periodicidade
            min_hours = 13 * 24 if cfg.periodicidade == "quinzenal" else 23
            if hours_since < min_hours:
                return False
        except Exception:
            pass  # se não conseguir parsear, deixa disparar

    return True


def mark_dispatched(tenant_id: str) -> None:
    """Registra o instante do último disparo no banco (campo last_dispatched_at).

    Deve ser chamado pelo scheduler imediatamente após um disparo bem-sucedido.
    Isso é a peça central da proteção anti-duplo-disparo.

    O campo `last_dispatched_at` precisa existir na tabela `email_schedule_config`.
    SQL de migração:
        ALTER TABLE email_schedule_config
        ADD COLUMN IF NOT EXISTS last_dispatched_at timestamptz;
    """
    from src.db.supabase_client import get_supabase_service
    sb = get_supabase_service()
    try:
        sb.table("email_schedule_config") \
          .update({"last_dispatched_at": now_utc_iso()}) \
          .eq("tenant_id", tenant_id) \
          .execute()
    except Exception as exc:
        log.warning(
            "mark_dispatched: falha ao registrar last_dispatched_at "
            "para tenant %s: %s", tenant_id, exc,
        )
