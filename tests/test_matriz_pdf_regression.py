from __future__ import annotations

import importlib.util
from io import BytesIO
from pathlib import Path

import pandas as pd
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src/ui/pages/matriz_pdf.py"

spec = importlib.util.spec_from_file_location("matriz_pdf_regression", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def _extract_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def test_build_pdf_tables_uses_current_group_data_on_consecutive_calls():
    resumo_a = pd.DataFrame([
        {"Setor": "Motor", "Equipamentos": 2, "Serviços": 6, "%": 80}
    ])
    resumo_b = pd.DataFrame([
        {"Setor": "Freios", "Equipamentos": 1, "Serviços": 3, "%": 40}
    ])

    setores_a = [(
        "Motor",
        pd.DataFrame([
            {"Equipamento": "F-100", "Lubrificação D": "OK", "Lubrificação R": "", "Lubrificação M": ""}
        ]),
    )]
    setores_b = [(
        "Freios",
        pd.DataFrame([
            {"Equipamento": "F-200", "Inspeção D": "PEND", "Inspeção R": "", "Inspeção M": ""}
        ]),
    )]

    pdf_a = mod._build_pdf_tables(
        titulo="Revisão Janeiro",
        grupo_nome="Grupo Alpha",
        resumo_df=resumo_a,
        sector_tables=setores_a,
    )
    pdf_b = mod._build_pdf_tables(
        titulo="Revisão Janeiro",
        grupo_nome="Grupo Beta",
        resumo_df=resumo_b,
        sector_tables=setores_b,
    )

    text_a = _extract_text(pdf_a)
    text_b = _extract_text(pdf_b)

    assert "Grupo Alpha" in text_a
    assert "F-100" in text_a
    assert "Lubrificação" in text_a
    assert "Grupo Beta" not in text_a
    assert "F-200" not in text_a

    assert "Grupo Beta" in text_b
    assert "F-200" in text_b
    assert "Inspeção" in text_b
    assert "Grupo Alpha" not in text_b
    assert "F-100" not in text_b
