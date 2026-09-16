"""Service workbook structure, repeated orders, and source time zone."""

from datetime import date, datetime
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook

from importers.excel_servicos import parse_excel


def test_reordered_headers_and_two_services_for_one_order():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Exportação com outro nome"
    sheet.append(["Cliente", "Cortes", "Código Interno", "Serviço", "Previsão Entrega",
                  "Chapas", "Enviado Produção", "Metros lineares de corte"])
    sheet.append(["Origem A", "282", "001234", "20533164", date(2026, 9, 21),
                  "11", datetime(2026, 9, 16, 10, 30), "227,924"])
    sheet.append(["Origem B", "10", "001234", "20533165", date(2026, 9, 22),
                  "2", datetime(2026, 9, 16, 11, 0), "10,5"])
    stream = BytesIO()
    workbook.save(stream)
    parsed = parse_excel(stream.getvalue())
    assert not parsed.errors
    assert len(parsed.rows) == 2
    assert [row.order_code for row in parsed.rows] == ["001234", "001234"]
    assert [row.service_code for row in parsed.rows] == ["20533164", "20533165"]
    assert parsed.rows[0].fields["metros_lineares_corte"] == Decimal("227.924")
    assert parsed.rows[0].fields["enviado_producao"].utcoffset().total_seconds() == -3 * 3600
