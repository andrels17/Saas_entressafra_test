"""Validação de saída de PDF.

Verifica integridade básica de bytes PDF gerados antes de enviá-los
por email, evitando anexar arquivos corrompidos ou vazios.

Uso:
    from src.services.reporting.pdf_validator import validate_pdf, PdfValidationError

    try:
        pdf_bytes = build_weekly_pdf(payload)
        validate_pdf(pdf_bytes, context="relatorio_semanal")
    except PdfValidationError as e:
        log.error("PDF inválido: %s", e)
        # não envia email com PDF corrompido
"""
from __future__ import annotations

import logging

log = logging.getLogger("saas.pdf_validator")

# Assinatura mágica de um arquivo PDF válido
_PDF_MAGIC = b"%PDF-"

# Limite mínimo razoável para um PDF com conteúdo (em bytes)
_MIN_SIZE_BYTES = 500

# Limite máximo para evitar PDFs muito grandes sendo enviados por email (20 MB)
_MAX_SIZE_BYTES = 20 * 1024 * 1024


class PdfValidationError(ValueError):
    """Lançada quando um PDF não passa na validação de integridade."""


def validate_pdf(
    pdf_bytes: bytes | None,
    *,
    context: str = "",
    min_size: int = _MIN_SIZE_BYTES,
    max_size: int = _MAX_SIZE_BYTES,
) -> None:
    """Valida integridade básica de um PDF.

    Verifica:
      1. Não é None nem vazio
      2. Começa com a assinatura %PDF-
      3. Tamanho está dentro dos limites esperados

    Args:
        pdf_bytes: Conteúdo do PDF como bytes.
        context:   Identificador para o log (ex: "relatorio_semanal").
        min_size:  Tamanho mínimo em bytes (default 500).
        max_size:  Tamanho máximo em bytes (default 20 MB).

    Raises:
        PdfValidationError: Se o PDF não passar em alguma verificação.
    """
    prefix = f"[{context}] " if context else ""

    if not pdf_bytes:
        msg = f"{prefix}PDF vazio ou None."
        log.error(msg)
        raise PdfValidationError(msg)

    size = len(pdf_bytes)

    if size > max_size:
        msg = f"{prefix}PDF muito grande ({
            size /
            1024 /
            1024:.1f} MB > máximo {
            max_size /
            1024 /
            1024:.0f} MB)."
        log.warning(msg)
        raise PdfValidationError(msg)

    if size < min_size:
        msg = f"{prefix}PDF muito pequeno ({size} bytes < mínimo {min_size}). Possível geração incompleta."
        log.error(msg)
        raise PdfValidationError(msg)

    if not pdf_bytes.startswith(_PDF_MAGIC):
        header = pdf_bytes[:10]
        msg = f"{prefix}Assinatura PDF inválida. Header: {header!r}"
        log.error(msg)
        raise PdfValidationError(msg)

    log.debug("%sPDF válido: %d bytes.", prefix, size)


def validate_pdf_safe(
    pdf_bytes: bytes | None,
    *,
    context: str = "",
) -> tuple[bool, str]:
    """Versão que retorna (ok, mensagem) em vez de lançar exceção.

    Útil para logging sem interromper o fluxo quando o PDF é opcional.

    Returns:
        (True, "") se válido, (False, mensagem_de_erro) se inválido.
    """
    try:
        validate_pdf(pdf_bytes, context=context)
        return True, ""
    except PdfValidationError as e:
        return False, str(e)
