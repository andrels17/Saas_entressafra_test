"""Dead-letter queue para emails com falha.

Quando send_email_with_retry() esgota todas as tentativas, o email
é gravado nesta fila para reprocessamento manual ou automático.

Schema da tabela `email_dead_letter`:
    id            uuid DEFAULT gen_random_uuid() PRIMARY KEY
    tenant_id     uuid REFERENCES tenants(id) ON DELETE CASCADE
    revisao_id    uuid
    recipient     text NOT NULL
    subject       text
    html_body     text
    pdf_filename  text
    pdf_bytes     bytea          -- armazenado apenas se < 5 MB
    error_message text
    attempts      int DEFAULT 1
    status        text DEFAULT 'pending'  -- 'pending' | 'retried' | 'abandoned'
    created_at    timestamptz DEFAULT now()
    retried_at    timestamptz

Índices recomendados:
    CREATE INDEX ON email_dead_letter (tenant_id, status, created_at DESC);

Uso:
    from src.services.email.dead_letter import enqueue_failed, retry_pending

    # No dispatcher, após falha de envio:
    enqueue_failed(tenant_id, revisao_id, recipient, subject, html_body,
                   pdf_bytes, pdf_filename, error=str(exc))

    # Botão manual na UI admin:
    results = retry_pending(tenant_id)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("saas.dead_letter")


def get_supabase_service():
    from src.db.supabase_client import get_supabase_service as _get_supabase_service

    return _get_supabase_service()


def _load_config_from_secrets():
    from src.services.email.smtp_sender import _load_config_from_secrets as _loader

    return _loader()


def send_email_with_retry(*args, **kwargs):
    from src.services.email.smtp_sender import send_email_with_retry as _send_email_with_retry

    return _send_email_with_retry(*args, **kwargs)


def _build_email_message(**kwargs):
    from src.services.email.smtp_sender import EmailMessage

    return EmailMessage(**kwargs)

_MAX_PDF_BYTES = 5 * 1024 * 1024  # 5 MB — limite para armazenar no banco


@dataclass
class RetryResult:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


# ── Escrita ─────────────────────────────────────────────────────────────

def enqueue_failed(
    tenant_id: str,
    revisao_id: str | None,
    recipient: str,
    subject: str,
    html_body: str,
    pdf_bytes: bytes | None,
    pdf_filename: str,
    error: str,
) -> bool:
    """Persiste um email com falha na dead-letter queue.

    Returns:
        True se gravou com sucesso, False caso contrário (falha nunca propaga).
    """
    try:
        svc = get_supabase_service()

        # Armazena PDF apenas se couber — evita inflar o banco
        pdf_col = None
        if pdf_bytes and len(pdf_bytes) <= _MAX_PDF_BYTES:
            import base64
            pdf_col = base64.b64encode(pdf_bytes).decode("ascii")

        payload = {
            "tenant_id": tenant_id,
            "revisao_id": revisao_id,
            "recipient": recipient,
            "subject": subject,
            "html_body": html_body,
            "pdf_filename": pdf_filename,
            "error_message": error[:2000],     # trunca erros muito longos
            "status": "pending",
            "attempts": 1,
        }
        if pdf_col:
            payload["pdf_bytes_b64"] = pdf_col

        svc.table("email_dead_letter").insert(payload).execute()
        log.warning(
            "Email enfileirado na dead-letter: to=%s subject=%s", recipient, subject[:60])
        return True

    except Exception as exc:
        log.error("Falha ao gravar na dead-letter queue: %s", exc)
        return False


# ── Reenvio ─────────────────────────────────────────────────────────────

def retry_pending(
    tenant_id: str,
    *,
    max_items: int = 50,
    progress_callback: Callable[[str], None] | None = None,
) -> RetryResult:
    """Reprocessa todos os emails pendentes na dead-letter de um tenant.

    Args:
        tenant_id:         Tenant a processar.
        max_items:         Limite de itens por execução (evita loops infinitos).
        progress_callback: Função opcional para reportar progresso (ex: st.write).

    Returns:
        RetryResult com contadores de sucesso e falha.
    """
    result = RetryResult()

    def _log(msg: str) -> None:
        log.info(msg)
        if progress_callback:
            try:
                progress_callback(msg)
            except Exception:
                pass  # ignorado — operação opcional

    try:
        svc = get_supabase_service()
        rows = (
            svc.table("email_dead_letter")
            .select("id,recipient,subject,html_body,pdf_filename,pdf_bytes_b64,attempts")
            .eq("tenant_id", tenant_id)
            .eq("status", "pending")
            .order("created_at")
            .limit(max_items)
            .execute()
            .data
        ) or []

        if not rows:
            _log("Nenhum email pendente na dead-letter queue.")
            return result

        result.total = len(rows)
        _log(f"{result.total} email(s) pendente(s) encontrado(s).")

        cfg = _load_config_from_secrets()

        for row in rows:
            row_id = row["id"]
            recipient = row.get("recipient", "")
            subject = row.get("subject", "")
            _log(f"  ↳ Reenviando para {recipient}…")

            # Reconstrói PDF bytes se disponível
            pdf_bytes = None
            if row.get("pdf_bytes_b64"):
                import base64
                try:
                    pdf_bytes = base64.b64decode(row["pdf_bytes_b64"])
                except Exception:
                    pass  # ignorado — operação opcional

            try:
                send_email_with_retry(
                    _build_email_message(
                        to=[recipient],
                        subject=subject,
                        html_body=row.get("html_body", ""),
                        pdf_bytes=pdf_bytes,
                        pdf_filename=row.get("pdf_filename", "relatorio.pdf"),
                    ),
                    cfg=cfg,
                )
                # Marca como enviado
                svc.table("email_dead_letter").update({
                    "status": "retried",
                    "retried_at": "now()",
                    "attempts": (row.get("attempts") or 1) + 1,
                }).eq("id", row_id).execute()
                result.succeeded += 1
                _log("    ✅ Reenviado.")

            except Exception as exc:
                # Incrementa contador e marca como abandonado após 3 tentativas
                attempts = (row.get("attempts") or 1) + 1
                new_status = "abandoned" if attempts >= 3 else "pending"
                svc.table("email_dead_letter").update({
                    "attempts": attempts,
                    "status": new_status,
                    "error_message": str(exc)[:2000],
                }).eq("id", row_id).execute()
                result.failed += 1
                msg = f"Falha ao reenviar para {recipient}: {exc}"
                result.errors.append(msg)
                _log(f"    ❌ {msg}")

    except Exception as exc:
        log.error("Erro ao processar dead-letter queue: %s", exc)
        result.errors.append(str(exc))

    return result


def get_pending_count(tenant_id: str) -> int:
    """Retorna o número de emails pendentes na dead-letter queue."""
    try:
        rows = (
            get_supabase_service()
            .table("email_dead_letter")
            .select("id", count="exact")
            .eq("tenant_id", tenant_id)
            .eq("status", "pending")
            .execute()
        )
        return rows.count or 0
    except Exception:
        return 0
