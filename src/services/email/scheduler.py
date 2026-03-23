#!/usr/bin/env python3
"""Scheduler standalone — disparo automático do relatório semanal."""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("scheduler")


class _EnvSecrets:
    """Imita st.secrets lendo variáveis de ambiente."""

    def __getitem__(self, key: str):
        value = os.environ.get(key)
        if value is None:
            raise KeyError(key)
        return value

    def get(self, key: str, default=None):
        return os.environ.get(key, default)


try:
    import streamlit as st
    if not hasattr(st, "_is_running_with_streamlit"):
        st.secrets = _EnvSecrets()  # type: ignore[attr-defined]
except ImportError:
    import types

    st_mod = types.ModuleType("streamlit")
    st_mod.secrets = _EnvSecrets()  # type: ignore[attr-defined]
    st_mod.cache_resource = lambda **kw: (lambda f: f)
    st_mod.cache_data = lambda **kw: (lambda f: f)
    sys.modules["streamlit"] = st_mod


@dataclass(frozen=True)
class SchedulerEnv:
    supabase_url: str
    supabase_service_role_key: str
    tenant_id: str
    revisao_id: str
    dry_run: bool
    force: bool
    dias_travado: int | None
    dias_sem_update: int | None


def _clean_env_value(name: str, allow_empty: bool = False) -> str:
    raw = os.environ.get(name, "")
    value = raw.strip()
    if not value:
        if allow_empty:
            return ""
        raise ValueError(f"{name} não definido.")
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        raise ValueError(
            f"{name} está com aspas no secret/env. Salve apenas o valor puro."
        )
    return value


def _parse_optional_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} deve ser inteiro, recebido: {raw!r}") from exc


def _load_env() -> SchedulerEnv:
    return SchedulerEnv(
        supabase_url=_clean_env_value("SUPABASE_URL"),
        supabase_service_role_key=_clean_env_value("SUPABASE_SERVICE_ROLE_KEY"),
        tenant_id=_clean_env_value("SCHEDULER_TENANT_ID"),
        revisao_id=_clean_env_value("SCHEDULER_REVISAO_ID", allow_empty=True),
        dry_run=os.environ.get("SCHEDULER_DRY_RUN", "0").strip() == "1",
        force=os.environ.get("SCHEDULER_FORCE", "0").strip() == "1",
        dias_travado=_parse_optional_int("SCHEDULER_DIAS_TRAV"),
        dias_sem_update=_parse_optional_int("SCHEDULER_DIAS_UPD"),
    )


def _log_env_summary(env: SchedulerEnv) -> None:
    log.info(
        "Ambiente validado | tenant=%s | dry_run=%s | force=%s | supabase_url=%s",
        env.tenant_id,
        env.dry_run,
        env.force,
        env.supabase_url,
    )


def _get_active_revisao(tenant_id: str) -> str | None:
    from src.db.supabase_client import get_supabase_service

    sb = get_supabase_service()
    try:
        rows = (
            sb.table("revisoes")
            .select("id,titulo,status")
            .eq("tenant_id", tenant_id)
            .eq("status", "ativa")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
            .data
        )
        if rows:
            revisao = rows[0]
            log.info(
                "Revisão ativa encontrada: %s (%s)",
                revisao.get("titulo"),
                revisao.get("id"),
            )
            return revisao["id"]
    except Exception as exc:
        log.error("Erro ao buscar revisão ativa: %s", exc)
    return None


def main() -> int:
    try:
        env = _load_env()
    except ValueError as exc:
        log.error("Configuração inválida: %s", exc)
        return 1

    _log_env_summary(env)

    from src.services.email.email_schedule import load_schedule_config, mark_dispatched, should_dispatch_now
    from src.services.email.dispatcher import dispatch_relatorio_semanal

    cfg = load_schedule_config(env.tenant_id)
    log.info("Config carregada: %s", cfg.descricao_humana())

    if not env.force and not env.dry_run:
        if not cfg.ativo:
            log.info("Agendamento desativado no painel. Abortando.")
            return 0
        if not should_dispatch_now(cfg):
            proximo = cfg.proximo_disparo_brt().strftime("%d/%m/%Y %H:%M")
            log.info(
                "Fora da janela de disparo. Próximo: %s BRT. Use SCHEDULER_FORCE=1 para forçar.",
                proximo,
            )
            return 0

    revisao_id = env.revisao_id or cfg.revisao_fixa or ""
    dias_trav = env.dias_travado if env.dias_travado is not None else cfg.dias_travado
    dias_upd = env.dias_sem_update if env.dias_sem_update is not None else cfg.dias_parado

    if not revisao_id:
        log.info("Revisão não especificada. Buscando revisão ativa…")
        revisao_id = _get_active_revisao(env.tenant_id)
        if not revisao_id:
            log.error("Nenhuma revisão ativa encontrada. Abortando.")
            return 1

    log.info("=== Disparo de Relatório Semanal ===")
    log.info("Tenant   : %s", env.tenant_id)
    log.info("Revisão  : %s", revisao_id)
    log.info("Config   : %s", cfg.descricao_humana())
    log.info("Dry run  : %s | Force: %s", env.dry_run, env.force)
    log.info("Dias trav: %d | Dias upd: %d", dias_trav, dias_upd)

    result = dispatch_relatorio_semanal(
        tenant_id=env.tenant_id,
        revisao_id=revisao_id,
        dias_travado=dias_trav,
        dias_sem_update=dias_upd,
        dry_run=env.dry_run,
        progress_callback=lambda msg: log.info(msg),
    )

    log.info("=== Resultado ===")
    log.info("Enviados : %d", result.sent)
    log.info("Falhas   : %d", result.failed)
    log.info("Pulados  : %d", result.skipped)
    for err in result.errors:
        log.error("  • %s", err)

    if not env.dry_run and result.sent > 0:
        mark_dispatched(env.tenant_id)
        log.info("last_dispatched_at atualizado para tenant %s.", env.tenant_id)

    return 0 if result.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
