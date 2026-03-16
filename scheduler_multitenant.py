#!/usr/bin/env python3
"""Scheduler multi-tenant — processa todos os tenants ativos em sequência.

Extensão do scheduler.py original que:
  - Itera sobre todos os tenants com agendamento ativo (ou lista explícita)
  - Processa cada tenant com seu próprio revisao_id (ativo ou fixo)
  - Coleta resultados e gera resumo consolidado
  - Respeita janelas de disparo individuais por tenant
  - Continua mesmo se um tenant falhar (isolamento de erros)

Uso:
  # Processar todos os tenants ativos
  python scheduler_multitenant.py

  # Processar apenas tenants específicos
  SCHEDULER_TENANT_IDS="uuid1,uuid2" python scheduler_multitenant.py

  # Dry run global
  SCHEDULER_DRY_RUN=1 python scheduler_multitenant.py

Variáveis de ambiente:
  SCHEDULER_TENANT_IDS   CSV de UUIDs (vazio = todos os tenants ativos)
  SCHEDULER_DRY_RUN      1 = não envia e-mails (default: 0)
  SCHEDULER_FORCE        1 = ignora janelas de disparo (default: 0)
  SCHEDULER_DIAS_TRAV    dias para alerta travado (default: lido do banco)
  SCHEDULER_DIAS_UPD     dias para alerta parado (default: lido do banco)
  SCHEDULER_PARALELO     1 = processa tenants em paralelo (default: 0)
  SCHEDULER_MAX_WORKERS  workers paralelos (default: 4)
"""
from __future__ import annotations

import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("scheduler_mt")


# ── Shim para st.secrets via variáveis de ambiente ───────────────────────────
class _EnvSecrets:
    def __getitem__(self, k: str):
        v = os.environ.get(k)
        if v is None:
            raise KeyError(k)
        return v

    def get(self, k: str, default=None):
        return os.environ.get(k, default)


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


# ── Estruturas de resultado ───────────────────────────────────────────────────

@dataclass
class TenantResult:
    tenant_id:   str
    tenant_nome: str = "?"
    sent:        int = 0
    failed:      int = 0
    skipped:     int = 0
    errors:      list[str] = field(default_factory=list)
    duration_s:  float = 0.0
    status:      str = "ok"   # "ok" | "skipped" | "error"


@dataclass
class MultiTenantReport:
    results:    list[TenantResult] = field(default_factory=list)
    total_sent: int = 0
    total_fail: int = 0
    duration_s: float = 0.0

    def summary(self) -> str:
        lines = [
            "=" * 60,
            f"RELATÓRIO MULTI-TENANT  — {len(self.results)} tenants",
            f"Enviados: {self.total_sent}  |  Falhas: {self.total_fail}  |  Tempo: {self.duration_s:.1f}s",
            "-" * 60,
        ]
        for r in self.results:
            icon = {"ok": "✅", "skipped": "⏭️", "error": "❌"}.get(r.status, "?")
            lines.append(
                f"  {icon} {r.tenant_nome[:40]:<40} "
                f"env={r.sent} fail={r.failed} ({r.duration_s:.1f}s)"
            )
            for err in r.errors[:3]:
                lines.append(f"      └─ {err[:100]}")
        lines.append("=" * 60)
        return "\n".join(lines)


# ── Busca tenants ativos ──────────────────────────────────────────────────────

def _get_active_tenants() -> list[dict]:
    """Retorna todos os tenants com agendamento ativo."""
    from src.db.supabase_client import get_supabase_service
    from src.services.email.email_schedule import load_schedule_config

    svc = get_supabase_service()
    try:
        tenants = (
            svc.table("tenants")
            .select("id,nome")
            .execute()
            .data
        ) or []
    except Exception as exc:
        log.error("Erro ao listar tenants: %s", exc)
        return []

    ativos = []
    for t in tenants:
        tid = t.get("id")
        if not tid:
            continue
        try:
            cfg = load_schedule_config(tid)
            if cfg.ativo:
                ativos.append(t)
            else:
                log.debug("Tenant %s (%s): agendamento desativado.", tid, t.get("nome"))
        except Exception:
            # Se não tem config de agendamento, inclui para não perder tenants novos
            ativos.append(t)

    return ativos


def _get_active_revisao(tenant_id: str) -> str | None:
    from src.db.supabase_client import get_supabase_service
    svc = get_supabase_service()
    try:
        rows = (
            svc.table("revisoes")
            .select("id,titulo,status")
            .eq("tenant_id", tenant_id)
            .eq("status", "ativa")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
            .data
        )
        return rows[0]["id"] if rows else None
    except Exception as exc:
        log.error("[%s] Erro ao buscar revisão ativa: %s", tenant_id, exc)
        return None


# ── Processamento de um único tenant ─────────────────────────────────────────

def _process_tenant(
    tenant: dict,
    *,
    dry_run: bool,
    force: bool,
    dias_trav: int | None,
    dias_upd: int | None,
) -> TenantResult:
    tenant_id   = tenant["id"]
    tenant_nome = tenant.get("nome") or tenant_id[:8]
    result      = TenantResult(tenant_id=tenant_id, tenant_nome=tenant_nome)
    t0          = time.monotonic()

    try:
        from src.services.email.email_schedule import (
            load_schedule_config, should_dispatch_now, mark_dispatched,
        )

        cfg = load_schedule_config(tenant_id)
        log.info("[%s] Config: %s", tenant_nome, cfg.descricao_humana())

        if not force and not dry_run:
            if not cfg.ativo:
                log.info("[%s] Agendamento desativado. Pulando.", tenant_nome)
                result.status = "skipped"
                return result
            if not should_dispatch_now(cfg):
                proximo = cfg.proximo_disparo_brt().strftime("%d/%m %H:%M")
                log.info("[%s] Fora da janela. Próximo: %s BRT.", tenant_nome, proximo)
                result.status = "skipped"
                return result

        revisao_id = cfg.revisao_fixa or _get_active_revisao(tenant_id)
        if not revisao_id:
            log.warning("[%s] Nenhuma revisão ativa. Pulando.", tenant_nome)
            result.status = "skipped"
            result.errors.append("Nenhuma revisão ativa encontrada.")
            return result

        _dias_trav = dias_trav if dias_trav is not None else cfg.dias_travado
        _dias_upd  = dias_upd  if dias_upd  is not None else cfg.dias_parado

        log.info("[%s] Disparando revisão=%s dias_trav=%d dias_upd=%d",
                 tenant_nome, revisao_id[:8], _dias_trav, _dias_upd)

        from src.services.email.dispatcher import dispatch_relatorio_semanal
        dispatch_result = dispatch_relatorio_semanal(
            tenant_id=tenant_id,
            revisao_id=revisao_id,
            dias_travado=_dias_trav,
            dias_sem_update=_dias_upd,
            dry_run=dry_run,
            progress_callback=lambda msg: log.info("[%s] %s", tenant_nome, msg),
        )

        result.sent    = dispatch_result.sent
        result.failed  = dispatch_result.failed
        result.skipped = dispatch_result.skipped
        result.errors  = list(dispatch_result.errors or [])
        result.status  = "ok" if dispatch_result.failed == 0 else "error"

        if not dry_run and dispatch_result.sent > 0:
            mark_dispatched(tenant_id)
            log.info("[%s] last_dispatched_at atualizado.", tenant_nome)

    except Exception as exc:
        log.error("[%s] Erro inesperado: %s", tenant_nome, exc, exc_info=True)
        result.status = "error"
        result.errors.append(str(exc))
        result.failed += 1
    finally:
        result.duration_s = time.monotonic() - t0

    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    dry_run   = os.environ.get("SCHEDULER_DRY_RUN",  "0").strip() == "1"
    force     = os.environ.get("SCHEDULER_FORCE",    "0").strip() == "1"
    paralelo  = os.environ.get("SCHEDULER_PARALELO", "0").strip() == "1"
    max_workers = int(os.environ.get("SCHEDULER_MAX_WORKERS", "4"))

    dias_trav_raw = os.environ.get("SCHEDULER_DIAS_TRAV", "")
    dias_upd_raw  = os.environ.get("SCHEDULER_DIAS_UPD",  "")
    dias_trav = int(dias_trav_raw) if dias_trav_raw.strip() else None
    dias_upd  = int(dias_upd_raw)  if dias_upd_raw.strip()  else None

    # Tenants: lista explícita ou todos os ativos
    tenant_ids_raw = os.environ.get("SCHEDULER_TENANT_IDS", "").strip()
    if tenant_ids_raw:
        tenants = [{"id": t.strip(), "nome": t.strip()[:8]}
                   for t in tenant_ids_raw.split(",") if t.strip()]
        log.info("Processando %d tenant(s) explícito(s).", len(tenants))
    else:
        log.info("Buscando tenants com agendamento ativo…")
        tenants = _get_active_tenants()
        log.info("Encontrados %d tenant(s) ativo(s).", len(tenants))

    if not tenants:
        log.warning("Nenhum tenant para processar. Encerrando.")
        return 0

    log.info("=== Scheduler Multi-Tenant ===")
    log.info("Tenants: %d  |  Dry run: %s  |  Force: %s  |  Paralelo: %s",
             len(tenants), dry_run, force, paralelo)

    kwargs = dict(dry_run=dry_run, force=force, dias_trav=dias_trav, dias_upd=dias_upd)
    t_global = time.monotonic()
    results: list[TenantResult] = []

    if paralelo and len(tenants) > 1:
        log.info("Modo paralelo: %d workers.", max_workers)
        with ThreadPoolExecutor(max_workers=min(max_workers, len(tenants))) as pool:
            futures = {pool.submit(_process_tenant, t, **kwargs): t for t in tenants}
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:
                    t = futures[future]
                    log.error("Worker falhou para tenant %s: %s", t.get("id"), exc)
                    results.append(TenantResult(
                        tenant_id=t["id"], tenant_nome=t.get("nome", "?"),
                        status="error", errors=[str(exc)], failed=1,
                    ))
    else:
        for tenant in tenants:
            results.append(_process_tenant(tenant, **kwargs))

    report = MultiTenantReport(
        results=results,
        total_sent=sum(r.sent for r in results),
        total_fail=sum(r.failed for r in results),
        duration_s=time.monotonic() - t_global,
    )

    log.info("\n%s", report.summary())

    return 0 if report.total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
