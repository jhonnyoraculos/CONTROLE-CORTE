"""Regression checks against the sales-order PDF supplied with the project."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pymupdf
import pytest

from importers.pdf_carrinho import parse_pdf


REAL_PDF = Path(
    r"C:\Users\JRFerragens\OneDrive - JR Ferragens & Madeiras"
    r"\Área de Trabalho\CARRINHOFIN.pdf"
)


@pytest.mark.skipif(not REAL_PDF.is_file(), reason="PDF original não disponível")
def test_supplied_cart_pdf() -> None:
    result = parse_pdf(REAL_PDF.read_bytes())

    assert result.order_code == "173368"
    assert result.cart_code == "109633"
    assert result.fields["data_pedido"] == date(2026, 9, 9)
    assert result.fields["data_carregamento"] == date(2026, 9, 21)
    assert result.fields["cliente_pdf"] == "GARBO MOVEIS SOB MEDIDA"
    assert result.fields["modalidade"] == "CD - ENTREGA SERVICO - EM PRODUCAO"
    assert result.fields["valor_produtos"] == Decimal("3736.31")
    assert result.fields["taxa_entrega"] == Decimal("79.90")
    assert result.fields["valor_total"] == Decimal("3816.21")
    assert result.fields["quantidade_total_pdf"] == Decimal("681.00")
    assert "CORTECLOUD 20533164" in result.fields["informacao_separacao_pdf"]
    assert [item["numero_item"] for item in result.items] == [
        "1", "2", "3", "5", "6", "7", "8", "9", "10"
    ]
    assert result.items[2]["valor_total"] == Decimal("812.59")
    assert result.items[2]["tipo_item"] == "MDF"
    assert result.items[6]["tipo_item"] == "SERVICO_CORTE"
    assert {item["tipo_item"] for item in result.items} == {
        "MDF", "FITA", "SERVICO_CORTE", "SERVICO_FITAMENTO", "SERVICO_USINAGEM"
    }
    assert result.fields["cliente_documento"] == "058.838.942/0001-23"
    assert any("Item 3" in warning for warning in result.warnings)


def test_image_only_pdf_reports_no_text_without_ocr() -> None:
    document = pymupdf.open()
    document.new_page()
    data = document.tobytes()
    document.close()

    result = parse_pdf(data)
    assert result.order_code is None
    assert result.items == []
    assert any("OCR não foi executado" in warning for warning in result.warnings)


def test_invalid_pdf_rejected() -> None:
    with pytest.raises(ValueError, match="PDF inválido"):
        parse_pdf(b"not a PDF")
