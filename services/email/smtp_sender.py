"""Envio de e-mail via SMTP com anexo PDF.

Configuração via st.secrets (ou variáveis de ambiente):
  SMTP_HOST      ex: smtp.office365.com  (Microsoft) ou smtp.gmail.com (Gmail)
  SMTP_PORT      ex: 587
  SMTP_USER      ex: relatorios@empresa.com
  SMTP_PASSWORD  senha ou app-password
  SMTP_FROM_NAME ex: Sistema AgroSafra  (opcional)
  SMTP_USE_TLS   true/false  (opcional — padrão: true para porta 587)
  SMTP_USE_SSL   true/false  (opcional — padrão: true para porta 465)
"""
from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List


@dataclass
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    from_name: str = "Sistema de Revisões"
    use_tls: bool = True          # STARTTLS na porta 587 (Gmail, Outlook, Office365)
    use_ssl: bool = False         # SSL direto na porta 465


@dataclass
class EmailMessage:
    to: List[str]                 # lista de destinatários
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

    missing = [k for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD")
               if not secrets.get(k)]
    if missing:
        raise ValueError(
            f"Configuração SMTP incompleta. Adicione ao secrets.toml: {', '.join(missing)}"
        )

    port = int(secrets["SMTP_PORT"])

    # Determina TLS/SSL: respeita override explícito, senão usa porta como padrão
    _true_vals = ("true", "1", "yes")
    use_tls_raw = secrets.get("SMTP_USE_TLS", "")
    use_ssl_raw = secrets.get("SMTP_USE_SSL", "")

    if use_tls_raw != "":
        use_tls = str(use_tls_raw).lower() in _true_vals
    else:
        use_tls = port == 587  # STARTTLS padrão para 587 (Gmail, Outlook, Office365)

    if use_ssl_raw != "":
        use_ssl = str(use_ssl_raw).lower() in _true_vals
    else:
        use_ssl = port == 465  # SSL direto padrão para 465

    return SmtpConfig(
        host=secrets["SMTP_HOST"],
        port=port,
        user=secrets["SMTP_USER"],
        password=secrets["SMTP_PASSWORD"],
        from_name=secrets.get("SMTP_FROM_NAME") or "Sistema de Revisões",
        use_tls=use_tls,
        use_ssl=use_ssl,
    )


def send_email(msg: EmailMessage, cfg: SmtpConfig | None = None) -> None:
    """Envia o e-mail. Levanta smtplib.SMTPException em caso de falha."""
    if cfg is None:
        cfg = _load_config_from_secrets()

    mime = MIMEMultipart("mixed")
    mime["From"]    = f"{cfg.from_name} <{cfg.user}>"
    mime["To"]      = ", ".join(msg.to)
    mime["Subject"] = msg.subject
    if msg.cc:
        mime["Cc"] = ", ".join(msg.cc)

    # Corpo HTML
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(msg.html_body, "html", "utf-8"))
    mime.attach(alt)

    # Anexo PDF
    if msg.pdf_bytes:
        part = MIMEApplication(msg.pdf_bytes, _subtype="pdf")
        part.add_header("Content-Disposition", "attachment",
                        filename=msg.pdf_filename)
        mime.attach(part)

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
    """Gera o corpo HTML do e-mail — objetivo, sem tabela de equipamentos (está no PDF)."""
    if pct_geral >= 80:
        bar_color = "#12B76A"
    elif pct_geral >= 50:
        bar_color = "#F59E0B"
    else:
        bar_color = "#EF4444"
    now = datetime.now().strftime("%d/%m/%Y")
    alerta_txt = (f"{n_alertas} alerta{'s' if n_alertas != 1 else ''} "
                  f"ativo{'s' if n_alertas != 1 else ''}") if n_alertas else "sem alertas"
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
                <div style="width:{min(pct_geral,100)}%;background:{bar_color};height:8px;border-radius:4px;"></div>
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
