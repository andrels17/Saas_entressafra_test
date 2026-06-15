#!/usr/bin/env python3
"""Scheduler standalone — disparo automático do relatório semanal.

Pode ser executado via:
  - Cron local:       0 8 * * 1 /path/to/venv/bin/python scheduler.py
  - GitHub Actions:   ver .github/workflows/relatorio_semanal.yml
  - Supabase CLI:     supabase functions invoke (com adaptação)

Variáveis de ambiente necessárias (ou secrets.toml):
  SUPABASE_URL
  SUPABASE_ANON_KEY
  SUPABASE_SERVICE_ROLE_KEY
  SMTP_HOST
  SMTP_PORT
  SMTP_USER
  SMTP_PASSWORD
  SMTP_FROM_NAME       (opcional)
  SCHEDULER_TENANT_ID  tenant a processar
  SCHEDULER_REVISAO_ID revisão ativa (se vazio, busca a mais recente ativa)
  SCHEDULER_DRY_RUN    1 = não envia e-mails (default: 0)
  SCHEDULER_DIAS_TRAV  dias para alerta travado (default: 2)
  SCHEDULER_DIAS_UPD   dias para alerta parado (default: 5)
"""
from __future__ import annotations

import logging
import os
import sys

# ── garante que o diretório raiz do projeto está no PYTHONPATH ───────────────
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("scheduler")


# ── Shim para st.secrets via variáveis de ambiente ───────────────────────────
# O smtp_sender e o supabase_client usam st.secrets.
# Fora do Streamlit, emulamos com um objeto simples lendo os env vars.

class _EnvSecrets:
    """Imita st.secrets lendo variáveis de ambiente."""
    def __getitem__(self, k: str):
        v = os.environ.get(k)
        if v is None:
            raise KeyError(k)
        return v

    def get(self, k: str, default=None):
        return os.environ.get(k, default)


# Injeta antes de qualquer import que use st.secrets
try:
    import streamlit as st
    # Se o Streamlit já existe mas não está em execução, substituímos secrets
    if not hasattr(st, "_is_running_with_streamlit"):
        st.secrets = _EnvSecrets()          # type: ignore[attr-defined]
except ImportError:
    # Cria módulo fake mínimo para que os imports não quebrem
    import types
    st_mod = types.ModuleType("streamlit")
    st_mod.secrets = _EnvSecrets()          # type: ignore[attr-defined]
    st_mod.cache_resource = lambda **kw: (lambda f: f)
    st_mod.cache_data = lambda **kw: (lambda f: f)
    sys.modules["streamlit"] = st_mod


# ── Busca revisão ativa se não especificada ───────────────────────────────────

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
            rev = rows[0]
            log.info("Revisão ativa encontrada: %s (%s)", rev.get("titulo"), rev.get("id"))
            return rev["id"]
    except Exception as e:
        log.error("Erro ao buscar revisão ativa: %s", e)
    return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    tenant_id = os.environ.get("SCHEDULER_TENANT_ID", "").strip()
    dry_run   = os.environ.get("SCHEDULER_DRY_RUN", "0").strip() == "1"
    force     = os.environ.get("SCHEDULER_FORCE", "0").strip() == "1"
    tolerance_minutes = int(os.environ.get("SCHEDULER_TOLERANCE_MINUTES", "10") or "10")

    if not tenant_id:
        log.error("SCHEDULER_TENANT_ID não definido. Abortando.")
        return 1

    # ── Carrega config do Supabase ─────────────────────────────────────────
    from src.services.email.email_schedule import (
        load_schedule_config, should_dispatch_now,
    )
    cfg = load_schedule_config(tenant_id)
    log.info("Config carregada: %s", cfg.descricao_humana())

    # Verifica janela de disparo (exceto em force/dry_run)
    if not force and not dry_run:
        if not cfg.ativo:
            log.info("Agendamento desativado no painel. Abortando.")
            return 0
        if not should_dispatch_now(cfg, tolerance_minutes=tolerance_minutes):
            proximo = cfg.proximo_disparo_brt().strftime("%d/%m/%Y %H:%M")
            log.info("Fora da janela de disparo. Próximo: %s BRT. "
                     "Use SCHEDULER_FORCE=1 para forçar. Tolerância atual: ±%d min.",
                     proximo, tolerance_minutes)
            return 0

    # Parâmetros: config do Supabase tem prioridade; env vars são fallback
    revisao_id = (os.environ.get("SCHEDULER_REVISAO_ID", "").strip()
                  or cfg.revisao_fixa or "")
    dias_trav  = int(os.environ.get("SCHEDULER_DIAS_TRAV", "") or cfg.dias_travado)
    dias_upd   = int(os.environ.get("SCHEDULER_DIAS_UPD",  "") or cfg.dias_parado)

    if not revisao_id:
        log.info("Revisão não especificada. Buscando revisão ativa…")
        revisao_id = _get_active_revisao(tenant_id)
        if not revisao_id:
            log.error("Nenhuma revisão ativa encontrada. Abortando.")
            return 1

    log.info("=== Disparo de Relatório Semanal ===")
    log.info("Tenant   : %s", tenant_id)
    log.info("Revisão  : %s", revisao_id)
    log.info("Config   : %s", cfg.descricao_humana())
    log.info("Dry run  : %s | Force: %s | Tolerância: ±%d min", dry_run, force, tolerance_minutes)
    log.info("Dias trav: %d | Dias upd: %d", dias_trav, dias_upd)

    from src.services.email.dispatcher import dispatch_relatorio_semanal
    from src.services.email.email_schedule import mark_dispatched

    result = dispatch_relatorio_semanal(
        tenant_id=tenant_id,
        revisao_id=revisao_id,
        dias_travado=dias_trav,
        dias_sem_update=dias_upd,
        dry_run=dry_run,
        progress_callback=lambda msg: log.info(msg),
    )

    log.info("=== Resultado ===")
    log.info("Enviados : %d", result.sent)
    log.info("Falhas   : %d", result.failed)
    log.info("Pulados  : %d", result.skipped)
    if result.errors:
        for err in result.errors:
            log.error("  • %s", err)

    # Registra o disparo no banco para proteger contra duplo envio
    if not dry_run and result.sent > 0:
        mark_dispatched(tenant_id)
        log.info("last_dispatched_at atualizado para tenant %s.", tenant_id)

    return 0 if result.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
