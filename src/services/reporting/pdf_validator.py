"""Health check do sistema — verifica conectividade e configuração.

Pode ser executado como script standalone:
    python healthcheck.py

Ou importado por qualquer página admin:
    from src.services.healthcheck import run_health_check
    status = run_health_check()

Checks realizados:
  1. Supabase anon key — conectividade básica
  2. Supabase service role — chave de administração
  3. Configuração SMTP — parâmetros presentes (sem enviar e-mail)
  4. Tabelas críticas — verifica se existem e são acessíveis
  5. Variáveis de ambiente / secrets — checklist mínimo

Retorna um HealthReport com status geral e detalhes por check.
"""
from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

log = logging.getLogger("saas.healthcheck")


class CheckStatus(str, Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    message: str
    latency_ms: float = 0.0


@dataclass
class HealthReport:
    checks: list[CheckResult] = field(default_factory=list)
    checked_at: str = ""

    @property
    def overall(self) -> CheckStatus:
        if any(c.status == CheckStatus.FAIL for c in self.checks):
            return CheckStatus.FAIL
        if any(c.status == CheckStatus.WARN for c in self.checks):
            return CheckStatus.WARN
        return CheckStatus.OK

    @property
    def ok(self) -> bool:
        return self.overall == CheckStatus.OK

    def summary(self) -> str:
        lines = [f"Health check — {self.checked_at}  [{self.overall.upper()}]"]
        for c in self.checks:
            icon = {
                "ok": "✅",
                "warn": "⚠️",
                "fail": "❌",
                "skip": "⏭️"}[
                c.status]
            lat = f" ({c.latency_ms:.0f}ms)" if c.latency_ms else ""
            lines.append(f"  {icon} {c.name}{lat}: {c.message}")
        return "\n".join(lines)


# ── Checks individuais ──────────────────────────────────────────────────

def _timed(fn: Callable) -> tuple[any, float]:
    """Executa fn() e retorna (resultado, latência_ms)."""
    t0 = time.monotonic()
    result = fn()
    return result, (time.monotonic() - t0) * 1000


def check_supabase_anon() -> CheckResult:
    try:
        from src.db.supabase_client import get_supabase_anon
        _, ms = _timed(lambda: get_supabase_anon().table(
            "tenants").select("id").limit(1).execute())
        return CheckResult("supabase_anon", CheckStatus.OK, "Conectado.", ms)
    except Exception as exc:
        return CheckResult("supabase_anon", CheckStatus.FAIL, f"Falha: {exc}")


def check_supabase_service() -> CheckResult:
    try:
        from src.db.supabase_client import get_supabase_service
        _, ms = _timed(lambda: get_supabase_service().table(
            "tenants").select("id").limit(1).execute())
        return CheckResult(
            "supabase_service",
            CheckStatus.OK,
            "Service role OK.",
            ms)
    except Exception as exc:
        return CheckResult(
            "supabase_service",
            CheckStatus.FAIL,
            f"Falha: {exc}")


def check_smtp_config() -> CheckResult:
    try:
        from src.services.email.smtp_sender import _load_config_from_secrets
        cfg = _load_config_from_secrets()
        return CheckResult(
            "smtp_config",
            CheckStatus.OK,
            f"Configurado: {cfg.host}:{cfg.port} (user={cfg.user})",
        )
    except ValueError as exc:
        return CheckResult(
            "smtp_config",
            CheckStatus.WARN,
            f"Incompleto: {exc}")
    except Exception as exc:
        return CheckResult("smtp_config", CheckStatus.FAIL, f"Erro: {exc}")


def check_critical_tables() -> CheckResult:
    """Verifica se as tabelas críticas do sistema existem e respondem."""
    TABLES = [
        "tenants", "tenant_users", "departamentos",
        "equip_grupos", "equipamentos", "revisoes", "tarefas_servico",
    ]
    try:
        from src.db.supabase_client import get_supabase_service
        svc = get_supabase_service()
        missing = []
        for table in TABLES:
            try:
                svc.table(table).select("id").limit(1).execute()
            except Exception:
                missing.append(table)
        if missing:
            return CheckResult(
                "critical_tables",
                CheckStatus.FAIL,
                f"Tabelas inacessíveis: {', '.join(missing)}",
            )
        return CheckResult(
            "critical_tables", CheckStatus.OK, f"{len(TABLES)} tabelas OK.")
    except Exception as exc:
        return CheckResult("critical_tables", CheckStatus.FAIL, f"Erro: {exc}")


def check_audit_table() -> CheckResult:
    """Verifica se a tabela de audit_logs existe (migration aplicada)."""
    try:
        from src.db.supabase_client import get_supabase_service
        get_supabase_service().table("audit_logs").select("id").limit(1).execute()
        return CheckResult(
            "audit_logs_table",
            CheckStatus.OK,
            "Tabela existe.")
    except Exception as exc:
        msg = str(exc)
        if "does not exist" in msg or "relation" in msg.lower():
            return CheckResult(
                "audit_logs_table",
                CheckStatus.WARN,
                "Tabela audit_logs não encontrada. Execute migrations/001_create_audit_logs.sql.",
            )
        return CheckResult(
            "audit_logs_table",
            CheckStatus.WARN,
            f"Não verificável: {exc}")


def check_secrets() -> CheckResult:
    """Verifica presença das secrets obrigatórias."""
    REQUIRED = [
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY"]
    try:
        import streamlit as st
        missing = [k for k in REQUIRED if not st.secrets.get(k)]
    except Exception:
        import os
        missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        return CheckResult(
            "secrets",
            CheckStatus.FAIL,
            f"Secrets ausentes: {', '.join(missing)}",
        )
    return CheckResult(
        "secrets",
        CheckStatus.OK,
        "Todas as secrets obrigatórias presentes.")


# ── Orquestrador ────────────────────────────────────────────────────────

def run_health_check(*, skip_db: bool = False) -> HealthReport:
    """Executa todos os checks e retorna um HealthReport.

    Args:
        skip_db: Se True, pula checks que requerem conexão com banco (útil em CI sem rede).
    """
    from datetime import datetime
    report = HealthReport(
        checked_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    report.checks.append(check_secrets())

    if skip_db:
        report.checks.append(
            CheckResult(
                "supabase_anon",
                CheckStatus.SKIP,
                "Pulado (skip_db=True)"))
        report.checks.append(
            CheckResult(
                "supabase_service",
                CheckStatus.SKIP,
                "Pulado (skip_db=True)"))
        report.checks.append(
            CheckResult(
                "critical_tables",
                CheckStatus.SKIP,
                "Pulado (skip_db=True)"))
        report.checks.append(
            CheckResult(
                "audit_logs_table",
                CheckStatus.SKIP,
                "Pulado (skip_db=True)"))
    else:
        report.checks.append(check_supabase_anon())
        report.checks.append(check_supabase_service())
        report.checks.append(check_critical_tables())
        report.checks.append(check_audit_table())

    report.checks.append(check_smtp_config())

    log.info(report.summary())
    return report


# ── Página admin Streamlit ──────────────────────────────────────────────

def render_health_check_page() -> None:
    """Renderiza painel de health check na UI do Streamlit (apenas admins).

    Uso em qualquer página admin:
        from src.services.healthcheck import render_health_check_page
        render_health_check_page()
    """
    import streamlit as st

    role = str(st.session_state.get("current_role", "")).lower()
    if role not in ("admin", "superadmin"):
        st.warning("Acesso restrito a admins.")
        return

    st.markdown("### 🩺 Health Check do sistema")
    st.caption(
        "Verifica conectividade com Supabase, configuração SMTP e tabelas críticas.")

    if st.button("▶ Executar verificação", type="primary"):
        with st.spinner("Verificando…"):
            report = run_health_check()

        overall_color = {"ok": "🟢", "warn": "🟡", "fail": "🔴"}[report.overall]
        st.markdown(
            f"**Status geral: {overall_color} {report.overall.upper()}**  — {report.checked_at}")

        for c in report.checks:
            icon = {
                "ok": "✅",
                "warn": "⚠️",
                "fail": "❌",
                "skip": "⏭️"}[
                c.status]
            lat = f" `{c.latency_ms:.0f}ms`" if c.latency_ms else ""
            st.markdown(f"{icon} **{c.name}**{lat} — {c.message}")


# ── Entry point standalone ──────────────────────────────────────────────

if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(
        0, os.path.dirname(
            os.path.dirname(
                os.path.dirname(
                    os.path.abspath(__file__)))))

    # Stub streamlit para execução standalone
    import types as _types
    if "streamlit" not in sys.modules:
        _st = _types.ModuleType("streamlit")

        class _CR:
            def __call__(self, f=None, **
                         kw): return f if f else (lambda fn: fn)
        _st.cache_resource = _CR()
        _st.session_state = {}
        sys.modules["streamlit"] = _st

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    report = run_health_check()
    log.info(report.summary())
    sys.exit(0 if report.ok else 1)
