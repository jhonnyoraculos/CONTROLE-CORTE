"""Regression checks for the separate historical spreadsheet import path."""

from datetime import date
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook

from importers.legacy_controle import inspect_legacy, parse_legacy


def _synthetic_workbook() -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "Outro nome"
    sheet.append([
        "DATA PEDIDO", 27, " Nº PEDIDO", "DATA CARREGAMENTO", "STATUS",
        "VENDEDOR", "LOJA VENDA", "MODALIDADE (ENTREGA/RETIRA)",
        "CIDADE", "PESO", "TOTAL", "QTD CHAPAS", "QTD CORTES",
        "CÓD PLANO/PROJETO",
    ])
    sheet.append([
        date(2026, 9, 15), "Cliente confirmado", "001234", date(2026, 9, 28),
        "4. EM PRODUÇÃO - CORTE", "Ana", "2953", "RETIRA", "Divinópolis",
        "1.234,56", "500,00", 2, 30, "20.533.164",
    ])
    sheet.append([
        date(2026, 9, 15), "Linha modelo", None, None,
        "4. EM PRODUÇÃO - CORTE",
    ])
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def test_numeric_header_must_be_mapped_explicitly() -> None:
    data = _synthetic_workbook()
    headers = inspect_legacy(data)
    assert len(headers) == 14
    assert headers[1]["column"] == "B"
    assert headers[1]["header"] == 27
    assert headers[1]["suggested_field"] is None
    assert headers[1]["requires_mapping"] is True

    rejected = parse_legacy(data, {})
    assert rejected.rows == []
    assert rejected.errors[0]["field"] == "B"

    accepted = parse_legacy(data, {"B": "cliente_nome"})
    assert len(accepted.rows) == 1
    assert accepted.errors == []
    assert accepted.rows[0].order_code == "001234"
    assert accepted.rows[0].service_code == "20533164"
    assert accepted.rows[0].fields["cliente_nome"] == "Cliente confirmado"
    assert str(accepted.rows[0].fields["peso"]) == "1234.56"
    assert len(accepted.preview) == 1
    assert "sem número de pedido" in accepted.warnings[-1]

    ignored = parse_legacy(data, {"27": "ignorar"})
    assert len(ignored.rows) == 1
    assert "cliente_nome" not in ignored.rows[0].fields


def test_real_legacy_file_preserves_dirty_values_for_review() -> None:
    downloads = Path.home() / "Downloads"
    matches = [
        path for path in downloads.glob("CS DIVIN*CONTROLE*.xlsx")
        if not path.name.endswith(" (1).xlsx")
    ]
    if not matches:
        pytest.skip("Planilha histórica de exemplo não está disponível")
    data = matches[0].read_bytes()
    headers = inspect_legacy(data)
    assert len(headers) == 30
    numeric_header = next(header for header in headers if header["header"] == 27)
    assert numeric_header["column"] == "F"
    assert numeric_header["requires_mapping"] is True
    assert numeric_header["suggested_field"] is None

    result = parse_legacy(data, {"F": "cliente_nome"})
    assert len(result.rows) == 7581
    assert len(result.preview) == 20
    assert result.rows[0].fields["cliente_nome"]
    assert result.errors  # Dates and numbers in the source really need review.
    assert any(row.raw_data.get("_needs_order_review") for row in result.rows)
