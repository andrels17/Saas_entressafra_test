"""Validação de integridade de PDFs antes do envio por e-mail.

Verifica se os bytes gerados constituem um PDF válido e legível,
evitando o envio de arquivos corrompidos aos destinatários.

Uso:
    from src.services.reporting.pdf_validator import validate_pdf, PdfValidationError

    try:
        validate_pdf(pdf_bytes, context="relatorio_semanal.Colhedora")
    except PdfValidationError as exc:
        log.error("PDF inválido: %s", exc)
"""
from __future__ import annotations

import logging

log = logging.getLogger("saas.pdf_validator")

# Tamanho mínimo razoável para um PDF com conteúdo (bytes)
_MIN_PDF_SIZE = 512
# Assinatura mágica de um PDF válido
_PDF_MAGIC = b"%PDF-"


class PdfValidationError(Exception):
    """Levantada quando um PDF gerado não passa na validação de integridade."""
    pass


def validate_pdf(pdf_bytes: bytes, *, context: str = "") -> None:
    """Valida se `pdf_bytes` é um PDF íntegro e não corrompido.

    Checks realizados:
      1. Não está vazio
      2. Tamanho mínimo razoável
      3. Começa com assinatura %PDF-
      4. Termina com %%EOF (opcional — avisa mas não rejeita)

    Args:
        pdf_bytes: Conteúdo do PDF em bytes.
        context:   String descritiva para logging (ex.: nome do departamento).

    Raises:
        PdfValidationError: Se o PDF for inválido ou corrompido.
    """
    ctx = f"[{context}] " if context else ""

    if not pdf_bytes:
        raise PdfValidationError(f"{ctx}PDF vazio — nenhum byte gerado.")

    if len(pdf_bytes) < _MIN_PDF_SIZE:
        raise PdfValidationError(
            f"{ctx}PDF suspeito: apenas {len(pdf_bytes)} bytes "
            f"(mínimo esperado: {_MIN_PDF_SIZE})."
        )

    if not pdf_bytes.startswith(_PDF_MAGIC):
        header = pdf_bytes[:8].hex()
        raise PdfValidationError(
            f"{ctx}PDF não começa com assinatura '%PDF-' "
            f"(header hex: {header})."
        )

    # Aviso se não termina com %%EOF (PDF truncado)
    tail = pdf_bytes[-128:]
    if b"%%EOF" not in tail:
        log.warning(
            "%sPDF pode estar truncado — '%%%%EOF' não encontrado nos últimos 128 bytes.",
            ctx,
        )
        # Não rejeita: alguns geradores omitem %%EOF e ainda são válidos.

    log.debug("%sPDF OK — %d bytes.", ctx, len(pdf_bytes))
