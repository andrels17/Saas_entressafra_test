"""Envio de e-mail via SMTP com anexo PDF — com retry e backoff exponencial.

Melhorias v2:
  - send_email_with_retry(): 3 tentativas com backoff 1s → 2s → 4s
  - Erros transitórios (timeout, conexão) são retentados
  - Erros permanentes (auth, destinatário inválido) falham imediatamente
  - Logging estruturado em cada tentativa

Configuração via st.secrets (ou variáveis de ambiente):
  SMTP_HOST        ex: smtp.office365.com ou smtp.gmail.com
  SMTP_PORT        ex: 587
  SMTP_USER        ex: relatorios@empresa.com
  SMTP_PASSWORD    senha ou app-password
  SMTP_FROM_NAME   ex: Sistema AgroSafra  (opcional)
  SMTP_USE_TLS     true/false  (opcional — padrão: true para porta 587)
  SMTP_USE_SSL     true/false  (opcional — padrão: true para porta 465)
  SMTP_MAX_RETRIES ex: 3  (opcional — padrão: 3)
  SMTP_RETRY_BASE  ex: 1  (opcional — segundos base do backoff, padrão: 1)
"""
from __future__ import annotations

import logging
import smtplib
import ssl
import time
from dataclasses import dataclass, field
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Callable, List

from src.utils.timezone import fmt_brt
from src.utils.observability import log_error

log = logging.getLogger("saas.smtp_sender")

# Erros permanentes — não adianta retentar
_PERMANENT_ERRORS = (
    smtplib.SMTPAuthenticationError,
    smtplib.SMTPRecipientsRefused,
    smtplib.SMTPSenderRefused,
)

# Erros transitórios — vale retentar
_TRANSIENT_ERRORS = (
    smtplib.SMTPConnectError,
    smtplib.SMTPServerDisconnected,
    smtplib.SMTPHeloError,
    TimeoutError,
    ConnectionRefusedError,
    OSError,
)


@dataclass
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    from_name: str = "Sistema de Revisões"
    use_tls: bool = True   # STARTTLS na porta 587
    use_ssl: bool = False  # SSL direto na porta 465
    max_retries: int = 3      # tentativas totais (1 original + 2 retries)
    retry_base: float = 1.0   # segundos base do backoff exponencial


@dataclass
class EmailMessage:
    to: List[str]
    subject: str
    html_body: str
    pdf_bytes: bytes | None = None
    pdf_filename: str = "relatorio.pdf"
    cc: List[str] = field(default_factory=list)


def _load_config_from_secrets() -> SmtpConfig:
    """Carrega configuração SMTP do st.secrets. Levanta ValueError se incompleto."""
    try:
        import streamlit as st
        secrets = st.secrets
    except Exception:
        import os

        class _FakeSecrets:
            def __getitem__(self, k): return os.environ[k]
            def get(self, k, d=None): return os.environ.get(k, d)
        secrets = _FakeSecrets()

    missing = [
        k for k in (
            "SMTP_HOST",
            "SMTP_PORT",
            "SMTP_USER",
            "SMTP_PASSWORD") if not secrets.get(k)]
    if missing:
        raise ValueError(
            f"Configuração SMTP incompleta. Adicione ao secrets.toml: {
                ', '.join(missing)}")

    port = int(secrets["SMTP_PORT"])
    _true_vals = ("true", "1", "yes")

    use_tls_raw = secrets.get("SMTP_USE_TLS", "")
    use_ssl_raw = secrets.get("SMTP_USE_SSL", "")
    use_tls = str(use_tls_raw).lower(
    ) in _true_vals if use_tls_raw != "" else (port == 587)
    use_ssl = str(use_ssl_raw).lower(
    ) in _true_vals if use_ssl_raw != "" else (port == 465)

    return SmtpConfig(
        host=secrets["SMTP_HOST"],
        port=port,
        user=secrets["SMTP_USER"],
        password=secrets["SMTP_PASSWORD"],
        from_name=secrets.get("SMTP_FROM_NAME") or "Sistema de Revisões",
        use_tls=use_tls,
        use_ssl=use_ssl,
        max_retries=int(secrets.get("SMTP_MAX_RETRIES") or 3),
        retry_base=float(secrets.get("SMTP_RETRY_BASE") or 1.0),
    )


def _build_mime(msg: EmailMessage, cfg: SmtpConfig) -> MIMEMultipart:
    """Constrói o objeto MIME a partir de um EmailMessage."""
    mime = MIMEMultipart("mixed")
    mime["From"] = f"{cfg.from_name} <{cfg.user}>"
    mime["To"] = ", ".join(msg.to)
    mime["Subject"] = msg.subject
    if msg.cc:
        mime["Cc"] = ", ".join(msg.cc)

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(msg.html_body, "html", "utf-8"))
    mime.attach(alt)

    if msg.pdf_bytes:
        part = MIMEApplication(msg.pdf_bytes, _subtype="pdf")
        part.add_header(
            "Content-Disposition",
            "attachment",
            filename=msg.pdf_filename)
        mime.attach(part)

    return mime


def _send_once(msg: EmailMessage, cfg: SmtpConfig) -> None:
    """Tenta enviar o e-mail uma única vez. Levanta smtplib.SMTPException em falha."""
    mime = _build_mime(msg, cfg)
    all_to = list(msg.to) + list(msg.cc)
    ctx = ssl.create_default_context()

    if cfg.use_ssl:
        with smtplib.SMTP_SSL(cfg.host, cfg.port, context=ctx) as server:
            server.login(cfg.user, cfg.password)
            server.sendmail(cfg.user, all_to, mime.as_bytes())
    else:
        with smtplib.SMTP(cfg.host, cfg.port) as server:
            if cfg.use_tls:
                server.starttls(context=ctx)
            server.login(cfg.user, cfg.password)
            server.sendmail(cfg.user, all_to, mime.as_bytes())


def send_email(msg: EmailMessage, cfg: SmtpConfig | None = None) -> None:
    """Envia o e-mail sem retry (interface original — mantida para compatibilidade).

    Para ambientes de produção prefira send_email_with_retry().
    """
    if cfg is None:
        cfg = _load_config_from_secrets()
    _send_once(msg, cfg)


def send_email_with_retry(
    msg: EmailMessage,
    cfg: SmtpConfig | None = None,
    *,
    on_retry: "Callable[[int, Exception], None] | None" = None,
) -> None:
    """Envia o e-mail com retry e backoff exponencial.

    Tentativas: max_retries (padrão 3), com espera de retry_base * 2^(tentativa-1).
    Exemplo com retry_base=1: 1s → 2s → 4s entre as tentativas.

    Args:
        msg: Mensagem a enviar.
        cfg: Configuração SMTP. Lida de st.secrets se None.
        on_retry: Callback opcional chamado a cada retry com (tentativa, exc).

    Raises:
        smtplib.SMTPException: Se todas as tentativas falharem.
        ValueError: Se a configuração SMTP estiver incompleta.
    """
    if cfg is None:
        cfg = _load_config_from_secrets()

    last_exc: Exception | None = None

    for attempt in range(1, cfg.max_retries + 1):
        try:
            _send_once(msg, cfg)
            if attempt > 1:
                log.info(
                    "Email enviado na tentativa %d para %s",
                    attempt,
                    msg.to)
            return  # sucesso

        except _PERMANENT_ERRORS as exc:
            # Erro permanente: não adianta retentar
            log_error(
                exc,
                context="smtp_sender.send_email_with_retry",
                extra={
                    "attempt": attempt,
                    "to": msg.to,
                    "error_type": "permanent",
                },
            )
            raise

        except Exception as exc:
            last_exc = exc
            is_last = attempt == cfg.max_retries

            log.warning(
                "Falha SMTP (tentativa %d/%d) para %s: %s",
                attempt, cfg.max_retries, msg.to, exc,
            )

            if on_retry:
                try:
                    on_retry(attempt, exc)
                except Exception:
                    pass

            if is_last:
                log_error(
                    exc,
                    context="smtp_sender.send_email_with_retry",
                    extra={
                        "attempt": attempt,
                        "to": msg.to,
                        "max_retries": cfg.max_retries,
                        "error_type": "transient_exhausted",
                    },
                )
                raise

            wait = cfg.retry_base * (2 ** (attempt - 1))
            log.info(
                "Aguardando %.1fs antes da tentativa %d…",
                wait,
                attempt + 1)
            time.sleep(wait)

    # Não deve chegar aqui, mas por segurança:
    if last_exc:
        raise last_exc


def build_html_body(
    *,
    destinatario_nome: str,
    departamento_nome: str,
    revisao_titulo: str,
    semana_atual: int,
    semanas_total: int,
    pct_geral: int,
    n_alertas: int,
    primary_color: str = "#FFD100",
    equipamentos: list | None = None,
) -> str:
    """Gera o corpo HTML do e-mail."""
    if pct_geral >= 80:
        bar_color = "#12B76A"
    elif pct_geral >= 50:
        bar_color = "#F59E0B"
    else:
        bar_color = "#EF4444"
    now = fmt_brt("%d/%m/%Y")
    alerta_txt = (
        f"{n_alertas} alerta{
            's' if n_alertas != 1 else ''} " f"ativo{
            's' if n_alertas != 1 else ''}") if n_alertas else "sem alertas"
    alerta_color = "#EF4444" if n_alertas else "#12B76A"

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Relatório Semanal</title></head>
<body style="margin:0;padding:0;background:#F3F4F6;font-family:Arial,Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F3F4F6;padding:32px 0;">
  <tr><td align="center">
    <table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08);">

      <!-- Header -->
      <tr><td style="background:#111827;padding:24px 32px;">
        <div style="font-size:20px;font-weight:700;color:#fff;">Relatório Semanal de Revisão</div>
        <div style="font-size:13px;color:rgba(255,255,255,.6);margin-top:4px;">{revisao_titulo} · Semana {semana_atual}/{semanas_total} · {now}</div>
      </td></tr>

      <!-- Saudação -->
      <tr><td style="padding:28px 32px 0;">
        <div style="font-size:15px;color:#374151;">Olá, <b>{destinatario_nome}</b>!</div>
        <div style="font-size:14px;color:#6B7280;margin-top:6px;">
          Segue o resumo semanal do departamento <b>{departamento_nome}</b>.
          O PDF com análise detalhada, ranking de equipamentos e alertas está anexo.
        </div>
      </td></tr>

      <!-- KPI cards -->
      <tr><td style="padding:24px 32px 28px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="background:#F9FAFB;border-radius:10px;border:1px solid #E5E7EB;">
          <tr>
            <td style="padding:20px 24px;" width="50%">
              <div style="font-size:12px;color:#6B7280;margin-bottom:4px;">Progresso geral</div>
              <div style="font-size:32px;font-weight:700;color:{bar_color};">{pct_geral}%</div>
              <div style="background:#E5E7EB;border-radius:4px;height:8px;margin-top:10px;">
                <div style="width:{min(pct_geral, 100)}%;background:{bar_color};height:8px;border-radius:4px;"></div>
              </div>
              <div style="font-size:11px;color:#9CA3AF;margin-top:6px;">Semana {semana_atual} de {semanas_total}</div>
            </td>
            <td style="padding:20px 24px;border-left:1px solid #E5E7EB;" width="50%">
              <div style="font-size:12px;color:#6B7280;margin-bottom:4px;">Alertas ativos</div>
              <div style="font-size:32px;font-weight:700;color:{alerta_color};">{n_alertas if n_alertas else "✓"}</div>
              <div style="font-size:13px;color:{alerta_color};margin-top:4px;font-weight:600;">{alerta_txt}</div>
              <div style="font-size:11px;color:#9CA3AF;margin-top:6px;">Veja detalhes no PDF anexo</div>
            </td>
          </tr>
        </table>
      </td></tr>

      <!-- Footer -->
      <tr><td style="background:#F9FAFB;padding:16px 32px;border-top:1px solid #E5E7EB;">
        <div style="font-size:12px;color:#6B7280;">
          📎 O PDF anexo inclui ranking de equipamentos, maiores evoluções da semana, equipamentos críticos e análise de alertas.
        </div>
        <div style="font-size:11px;color:#9CA3AF;margin-top:8px;">
          Relatório gerado automaticamente — sistema de gestão de revisões.
        </div>
      </td></tr>

    </table>
  </td></tr>
</table>
</body></html>"""
