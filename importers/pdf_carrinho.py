"""Text-only parser for the JR Ferragens sales-order/cart PDF.

The parser returns source values and diagnostics. It never writes to the database,
and it does not attempt OCR when a PDF has no selectable text.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import pymupdf

from importers.normalizers import normalize_identifier, parse_date, parse_decimal


ORDER_FIELDS = (
    "data_pedido", "codigo_cliente_pdf", "cliente_pdf", "cliente_documento", "cliente_contato",
    "vendedor_pdf", "loja_venda", "tipo_ordem_venda", "modalidade",
    "endereco", "bairro", "cidade", "cep", "peso", "quantidade_total_pdf", "valor_produtos",
    "taxa_entrega", "desconto", "valor_total", "condicao_pagamento",
    "informacao_separacao_pdf", "observacao_pdf", "data_carregamento",
)


@dataclass(slots=True)
class PdfResult:
    order_code: str | None
    cart_code: str | None
    fields: dict[str, object]
    items: list[dict[str, Any]]
    warnings: list[str]
    raw_text: str


def _fold(value: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(ch)
    )


def _value_after_label(lines: list[str], label: str) -> str | None:
    target = _fold(label)
    for line in lines:
        folded = _fold(line)
        match = re.search(rf"(?<!\w){re.escape(target)}\s*:\s*", folded)
        if match:
            value = line[match.end():].strip()
            if value:
                return value
    return None


def _date(value: str | None, field: str, warnings: list[str]) -> date | None:
    if not value:
        return None
    initial_warning_count = len(warnings)
    try:
        parsed = parse_date(value, warnings=warnings)
    except ValueError as exc:
        warnings.append(f"{field}: data inválida ({value!r}): {exc}")
        return None
    if parsed is None and len(warnings) == initial_warning_count:
        warnings.append(f"{field}: data não interpretada ({value!r}).")
    if parsed is None:
        return None
    return parsed.date() if hasattr(parsed, "date") else parsed


def _decimal(value: str | None, field: str, warnings: list[str]) -> Decimal | None:
    if not value:
        return None
    try:
        return parse_decimal(value)
    except ValueError as exc:
        warnings.append(f"{field}: número inválido ({value!r}): {exc}")
        return None


def _first_number(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"\d[\d.,]*", value)
    return match.group() if match else None


def _words(page: pymupdf.Page) -> list[tuple[float, float, str]]:
    return [(word[0], word[1], word[4]) for word in page.get_text("words")]


def _find_client(page: pymupdf.Page) -> tuple[str | None, str | None, str | None]:
    words = _words(page)
    label = next(
        ((x, y) for x, y, text in words
         if _fold(text) == "cliente" and x < page.rect.width * .18
         and y < page.rect.height * .4),
        None,
    )
    if label is None:
        return None, None, None
    _, y = label
    contact_label = next(
        (x for x, wy, text in words
         if _fold(text) == "contato" and abs(wy - y) < 4),
        page.rect.width * .7,
    )
    name_words = sorted(
        ((x, text) for x, wy, text in words
         if y + 6 < wy < y + 19 and x < contact_label - 8),
        key=lambda pair: pair[0],
    )
    contact_words = sorted(
        ((x, text) for x, wy, text in words
         if y + 6 < wy < y + 19 and x >= contact_label - 2),
        key=lambda pair: pair[0],
    )
    name_line = " ".join(text for _, text in name_words)
    match = re.match(r"\s*([^\s-]+)\s*-\s*(.+)", name_line)
    if not match:
        return None, name_line or None, " ".join(text for _, text in contact_words) or None
    return (
        normalize_identifier(match.group(1)) or None,
        match.group(2).strip() or None,
        " ".join(text for _, text in contact_words).strip() or None,
    )


def _find_client_text(lines: list[str]) -> tuple[str | None, str | None, str | None]:
    for index, line in enumerate(lines):
        folded = _fold(line)
        if re.search(r"\bcliente\b", folded) and re.search(r"\bcontato\b", folded):
            for candidate in lines[index + 1:index + 4]:
                match = re.match(r"^\s*(\S+)\s*-\s*(.+?)\s{2,}(\S.*?)\s*$", candidate)
                if match:
                    return normalize_identifier(match.group(1)), match.group(2).strip(), match.group(3).strip()
    return None, None, None


def _summary(page: pymupdf.Page) -> tuple[str | None, Decimal | None, Decimal | None, str | None]:
    words = _words(page)
    label_y = next(
        (y for x, y, text in words if x < page.rect.width * .12
         and _fold(text) == "tipo" and page.rect.height * .25 < y < page.rect.height * .75),
        None,
    )
    if label_y is None:
        return None, None, None, None
    scale = page.rect.width / 595.0
    row = [(x / scale, text) for x, y, text in words if label_y + 8 < y < label_y + 23]
    modality = " ".join(text for x, text in sorted(row) if x < 251).strip() or None
    weight_text = " ".join(text for x, text in sorted(row) if 251 <= x < 323).strip()
    quantity_text = " ".join(text for x, text in sorted(row) if 323 <= x < 400).strip()
    store = " ".join(text for x, text in sorted(row) if x >= 472).strip() or None
    try:
        weight = parse_decimal(weight_text) if weight_text else None
    except ValueError:
        weight = None
    try:
        quantity = parse_decimal(quantity_text) if quantity_text else None
    except ValueError:
        quantity = None
    return modality, weight, quantity, store


def _summary_text(lines: list[str]) -> tuple[str | None, Decimal | None, Decimal | None, str | None]:
    for index, line in enumerate(lines):
        folded = _fold(line)
        if "tipo de saida" in folded and "peso" in folded and "qtde" in folded:
            for candidate in lines[index + 1:index + 4]:
                match = re.match(r"^\s*(.+?)\s{2,}([\d.,]+)\s+([\d.,]+)\s+"
                                 r"([\d.,]+)\s+(\d+)\s*$", candidate)
                if match:
                    try:
                        return (match.group(1).strip(), parse_decimal(match.group(2)),
                                parse_decimal(match.group(3)), match.group(5))
                    except ValueError:
                        break
    return None, None, None, None


def _document_type(lines: list[str]) -> str | None:
    for index, line in enumerate(lines):
        if _fold(line.strip()) == "documento":
            for candidate in lines[index + 1:index + 4]:
                if candidate.strip():
                    return candidate.strip()
    return None


def _section(lines: list[str], label: str, stop_labels: tuple[str, ...]) -> str | None:
    start = None
    for index, line in enumerate(lines):
        match = re.search(re.escape(_fold(label)) + r"\s*:", _fold(line))
        if match:
            start = index
            first = line[match.end():].strip()
            break
    if start is None:
        return None
    values = [first] if first else []
    for line in lines[start + 1:]:
        stripped = line.strip()
        if not stripped:
            if values:
                break
            continue
        folded = _fold(stripped)
        if stripped.startswith("----") or any(
            folded.startswith(_fold(stop)) for stop in stop_labels
        ):
            break
        values.append(stripped)
    return "\n".join(values) or None


def _load_date(separation: str | None, order_date: date | None,
               warnings: list[str]) -> date | None:
    if not separation:
        return None
    match = re.search(r"\bCARGA\s*(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?\b",
                      separation, flags=re.IGNORECASE)
    if not match:
        return None
    day, month, year = match.groups()
    if year is None:
        if order_date is None:
            warnings.append("Data de carregamento sem ano; não foi possível inferi-lo.")
            return None
        year = str(order_date.year)
        warnings.append(f"Ano da carga inferido da data do pedido: {year}.")
    elif len(year) == 2:
        year = "20" + year
    return _date(f"{day.zfill(2)}/{month.zfill(2)}/{year}", "data_carregamento", warnings)


def _classify_item(description: str, unit: str | None) -> str:
    folded = _fold(description).upper()
    if "SERVICO CORTE" in folded:
        return "SERVICO_CORTE"
    if "SERVICO FITAMENTO" in folded or "SERVICO FITAGEM" in folded:
        return "SERVICO_FITAMENTO"
    if "SERVICO USINAGEM" in folded:
        return "SERVICO_USINAGEM"
    if folded.startswith("SERVICO") or unit == "SV":
        return "OUTRO"
    if folded.startswith("FITA"):
        return "FITA"
    if folded.startswith(("MDF", "MDP", "CHAPA", "COMPENSADO")):
        return "MDF"
    return "OUTRO"


_TEXT_ITEM = re.compile(
    r"^\s*(?P<numero_item>\d{1,4})\s+(?P<codigo_produto>[A-Z0-9.-]+)\s+"
    r"(?P<descricao>.+?)\s{2,}(?P<ncm>\d{8})\s+"
    r"(?:(?P<peso>[\d.,]+)\s+)?(?P<unidade>[A-Z]{1,4})\s+"
    r"(?P<quantidade>[\d.,]+)\s+(?P<valor_unitario>[\d.,]+)\s+"
    r"(?P<valor_total>[\d.,]+)\s*$",
    re.I,
)


def _table_items_text(page: pymupdf.Page, page_number: int) -> tuple[list[dict[str, Any]], list[str], int]:
    """Prefer selectable row text; layout coordinates are only a fallback."""
    inside = False
    expected = 0
    warnings: list[str] = []
    result: list[dict[str, Any]] = []
    for line in page.get_text(sort=True).splitlines():
        folded = _fold(line)
        if not inside:
            if re.search(r"\bitem\b", folded) and "ncm" in folded and "total" in folded:
                inside = True
            continue
        if "taxa de entrega" in folded or folded.strip().startswith("resumo"):
            break
        if not re.match(r"^\s*\d{1,4}\s+\S+", line):
            continue
        expected += 1
        match = _TEXT_ITEM.match(line)
        if not match:
            continue
        raw = {key: (value or "") for key, value in match.groupdict().items()}
        number = raw["numero_item"]
        item = {
            "numero_item": number,
            "codigo_produto": raw["codigo_produto"],
            "descricao": raw["descricao"].strip(),
            "ncm": raw["ncm"],
            "peso": _decimal(raw["peso"], f"item {number}: peso", warnings),
            "unidade": raw["unidade"],
            "quantidade": _decimal(raw["quantidade"], f"item {number}: quantidade", warnings),
            "valor_unitario": _decimal(raw["valor_unitario"], f"item {number}: valor unitário", warnings),
            "valor_total": _decimal(raw["valor_total"], f"item {number}: valor total", warnings),
            "tipo_item": _classify_item(raw["descricao"], raw["unidade"]),
            "raw_data": {"page": page_number, "text": line.strip(), "printed_cells": raw},
        }
        if all(item[key] is not None for key in ("quantidade", "valor_unitario", "valor_total")):
            calculated = item["quantidade"] * item["valor_unitario"]
            if abs(calculated - item["valor_total"]) >= Decimal("0.01"):
                warnings.append(f"Item {number}: total impresso {item['valor_total']} difere de "
                                f"quantidade × unitário ({calculated}); valor impresso preservado.")
        result.append(item)
    return result, warnings, expected


def _table_items(page: pymupdf.Page, page_number: int,
                 warnings: list[str]) -> list[dict[str, Any]]:
    words = _words(page)
    header = next(
        ((x, y) for x, y, text in words if _fold(text) == "item"
         and x < page.rect.width * .08),
        None,
    )
    if header is None:
        return []
    _, header_y = header
    scale = page.rect.width / 595.0
    end_y = min(
        (y for _, y, text in words if y > header_y + 10
         and _fold(text) in {"taxa", "resumo"}),
        default=page.rect.height,
    )
    candidates = sorted(
        ((x / scale, y, text) for x, y, text in words
         if header_y + 7 < y < end_y - 2),
        key=lambda entry: (entry[1], entry[0]),
    )
    groups: list[list[tuple[float, float, str]]] = []
    for word in candidates:
        if not groups or word[1] - groups[-1][0][1] > 3:
            groups.append([word])
        else:
            groups[-1].append(word)

    bounds = (35, 72, 327, 369, 416, 438, 480, 544)
    names = ("numero_item", "codigo_produto", "descricao", "ncm", "peso",
             "unidade", "quantidade", "valor_unitario", "valor_total")
    result: list[dict[str, Any]] = []
    for group in groups:
        cells: dict[str, list[str]] = {name: [] for name in names}
        for x, _, word in sorted(group, key=lambda entry: entry[0]):
            index = next((i for i, bound in enumerate(bounds) if x < bound), 8)
            cells[names[index]].append(word)
        raw = {name: " ".join(values).strip() for name, values in cells.items()}
        item_number = raw["numero_item"]
        if not re.fullmatch(r"\d+", item_number):
            if result and raw["descricao"] and not any(
                raw[name] for name in ("codigo_produto", "ncm", "quantidade", "valor_total")
            ):
                result[-1]["descricao"] += " " + raw["descricao"]
                result[-1]["raw_data"]["text"] += " " + raw["descricao"]
            continue
        if not raw["codigo_produto"] or not raw["descricao"]:
            warnings.append(f"Página {page_number}, item {item_number}: linha incompleta.")
            continue
        item = {
            "numero_item": item_number,
            "codigo_produto": raw["codigo_produto"],
            "descricao": raw["descricao"],
            "ncm": raw["ncm"] or None,
            "peso": _decimal(raw["peso"], f"item {item_number}: peso", warnings),
            "unidade": raw["unidade"] or None,
            "quantidade": _decimal(raw["quantidade"], f"item {item_number}: quantidade", warnings),
            "valor_unitario": _decimal(raw["valor_unitario"], f"item {item_number}: valor unitário", warnings),
            "valor_total": _decimal(raw["valor_total"], f"item {item_number}: valor total", warnings),
            "tipo_item": _classify_item(raw["descricao"], raw["unidade"] or None),
            "raw_data": {"page": page_number, "text": " ".join(
                word for _, _, word in sorted(group, key=lambda entry: entry[0])
            ), "printed_cells": raw},
        }
        if item["quantidade"] is not None and item["valor_unitario"] is not None \
                and item["valor_total"] is not None:
            calculated = item["quantidade"] * item["valor_unitario"]
            if abs(calculated - item["valor_total"]) >= Decimal("0.01"):
                warnings.append(
                    f"Item {item_number}: total impresso {item['valor_total']} difere de "
                    f"quantidade × unitário ({calculated}); valor impresso preservado."
                )
        result.append(item)
    return result


def parse_pdf(data: bytes) -> PdfResult:
    """Extract a cart PDF into Order/OrderItem shaped values without persistence.

    Raises ValueError for invalid or encrypted PDF bytes. An image-only PDF returns
    an empty result with a warning, because this parser deliberately uses no OCR.
    """
    if not isinstance(data, bytes) or not data:
        raise ValueError("O PDF deve ser fornecido como bytes não vazios.")
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ValueError("Arquivo PDF inválido ou ilegível.") from exc
    try:
        if document.needs_pass:
            raise ValueError("PDF protegido por senha.")
        pages = list(document)
        raw_text = "\n\n".join(page.get_text(sort=True) for page in pages)
        warnings: list[str] = []
        fields: dict[str, object] = dict.fromkeys(ORDER_FIELDS)
        if not raw_text.strip():
            warnings.append("PDF sem texto selecionável; OCR não foi executado.")
            return PdfResult(None, None, fields, [], warnings, raw_text)

        lines = raw_text.splitlines()
        order_raw = _value_after_label(lines, "Pedido/Nota")
        cart_raw = _value_after_label(lines, "Carrinho")
        order_code = normalize_identifier(order_raw.split()[0] if order_raw else None) or None
        cart_code = normalize_identifier(cart_raw.split()[0] if cart_raw else None) or None
        if order_code is None:
            warnings.append("Número de pedido/nota não encontrado no PDF.")
        if cart_code is None:
            warnings.append("Número de carrinho não encontrado no PDF.")

        date_match = re.search(
            r"\bData:\s*(\d{1,2}[/.-]\d{1,2}[/.-]\d{4})", raw_text,
            flags=re.IGNORECASE,
        )
        fields["data_pedido"] = _date(
            date_match.group(1) if date_match else None, "data_pedido", warnings
        )
        fields["vendedor_pdf"] = _value_after_label(lines, "Vendedor")
        fields["cliente_documento"] = _value_after_label(lines, "CPF/CNPJ")
        fields["tipo_ordem_venda"] = _document_type(lines)

        code, client, contact = _find_client_text(lines)
        if not client:
            for page in pages:
                code, client, contact = _find_client(page)
                if client:
                    break
        fields["codigo_cliente_pdf"] = code
        fields["cliente_pdf"] = client
        fields["cliente_contato"] = contact
        modality, weight, quantity, store = _summary_text(lines)
        if not modality:
            for page in pages:
                modality, weight, quantity, store = _summary(page)
                if modality:
                    break
        fields["modalidade"] = modality
        fields["peso"] = weight
        fields["quantidade_total_pdf"] = quantity
        fields["loja_venda"] = store

        fields["endereco"] = _value_after_label(lines, "Endereço")
        fields["bairro"] = _value_after_label(lines, "Bairro")
        fields["cidade"] = _value_after_label(lines, "Cidade")
        fields["cep"] = _first_number(_value_after_label(lines, "CEP"))
        fields["taxa_entrega"] = _decimal(
            _first_number(_value_after_label(lines, "Taxa de Entrega")),
            "taxa_entrega", warnings,
        )
        fields["desconto"] = _decimal(
            _first_number(_value_after_label(lines, "Desconto")),
            "desconto", warnings,
        )
        fields["valor_total"] = _decimal(
            _first_number(_value_after_label(lines, "Total")),
            "valor_total", warnings,
        )
        fields["condicao_pagamento"] = _section(
            lines, "Condições de Pagamento", ("Endereço de Entrega",)
        )
        separation = _section(
            lines, "Informações para Separação", ("Cliente:", "CPF/CNPJ:", "Pedido:")
        )
        fields["informacao_separacao_pdf"] = separation
        aux = _section(lines, "Observações Auxiliares dos Pedidos", ("Informações NFe",))
        nfe = _section(lines, "Informações NFe", ("Informações para Separação",))
        observations = []
        if aux:
            observations.append("Observações auxiliares: " + aux)
        if nfe:
            observations.append("Informações NFe: " + nfe)
        fields["observacao_pdf"] = "\n".join(observations) or None
        fields["data_carregamento"] = _load_date(
            separation, fields["data_pedido"], warnings
        )

        items = []
        for number, page in enumerate(pages, 1):
            text_items, text_warnings, expected = _table_items_text(page, number)
            if expected and len(text_items) == expected:
                items.extend(text_items)
                warnings.extend(text_warnings)
            else:
                items.extend(_table_items(page, number, warnings))
        if not items:
            warnings.append("Tabela de produtos não encontrada ou sem itens legíveis.")
        printed_sum = sum(
            (item["valor_total"] for item in items if item["valor_total"] is not None),
            Decimal("0"),
        )
        total = fields["valor_total"]
        fee = fields["taxa_entrega"] or Decimal("0")
        discount = fields["desconto"] or Decimal("0")
        if items and any(item["valor_total"] is not None for item in items):
            fields["valor_produtos"] = printed_sum
            if total is not None and printed_sum + fee - discount != total:
                warnings.append(
                    "Soma dos itens, entrega e desconto difere do total impresso; "
                    "totais impressos foram preservados."
                )
        elif total is not None:
            fields["valor_produtos"] = total - fee + discount
        return PdfResult(order_code, cart_code, fields, items, warnings, raw_text)
    finally:
        document.close()
