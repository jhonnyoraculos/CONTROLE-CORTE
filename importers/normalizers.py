"""Pure conversion helpers for values read from spreadsheets and documents.

Identifiers are deliberately strings. In particular, text identifiers retain
leading zeroes even when their numeric value would be the same.
"""

from __future__ import annotations

import math
import re
import unicodedata
import warnings as python_warnings
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any

from openpyxl.utils.datetime import from_excel


def normalize_identifier(value: Any) -> str:
    """Normalize one code without converting it to a floating point number.

    Numeric Excel values and text such as ``173368.0`` become ``173368``.
    Formatting dots in ``20.533.164`` are removed. A compound value such as
    ``173091 173167`` remains compound so it cannot match an unrelated order.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        raise ValueError("Identificador booleano inválido")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError("Identificador numérico não inteiro")
        if abs(value) > 2**53:
            raise ValueError("Identificador Excel grande demais para converter sem perda")
        return str(int(value))
    if isinstance(value, Decimal):
        if not value.is_finite() or value != value.to_integral_value():
            raise ValueError("Identificador numérico não inteiro")
        return format(value.to_integral_value(), "f")

    text = unicodedata.normalize("NFKC", str(value)).replace("\xa0", " ").strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return ""
    if re.fullmatch(r"\d{1,3}(?:\.\d{3}){2,}", text):
        return text.replace(".", "")
    if re.fullmatch(r"\d{1,3}\.\d{3}", text):
        return text.replace(".", "")
    if re.fullmatch(r"[+]?\d+(?:\.0+)?", text):
        return re.sub(r"\.0+$", "", text.lstrip("+"))
    if re.fullmatch(r"[+]?\d+(?:[.,]\d+)?[eE][+-]?\d+", text):
        try:
            number = Decimal(text.replace(",", "."))
        except InvalidOperation as exc:
            raise ValueError("Identificador em notação científica inválido") from exc
        if not number.is_finite() or number != number.to_integral_value():
            raise ValueError("Identificador em notação científica não inteiro")
        return format(number.to_integral_value(), "f")
    return text.upper()


def parse_decimal(value: Any) -> Decimal | None:
    """Parse Excel numbers and Brazilian/English numeric strings as Decimal."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError("Valor numérico booleano inválido")
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Valor numérico não finito")
        number = Decimal(str(value))
    else:
        text = unicodedata.normalize("NFKC", str(value)).replace("\xa0", " ")
        text = text.strip().replace("R$", "").replace(" ", "")
        negative = text.startswith("(") and text.endswith(")")
        if negative:
            text = text[1:-1]
        if not re.fullmatch(r"[+-]?[\d.,]+", text):
            raise ValueError(f"Valor numérico inválido: {value!r}")
        if "," in text and "." in text:
            decimal_sep = "," if text.rfind(",") > text.rfind(".") else "."
            group_sep = "." if decimal_sep == "," else ","
            text = text.replace(group_sep, "").replace(decimal_sep, ".")
        elif "," in text:
            if text.count(",") > 1 and re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+", text):
                text = text.replace(",", "")
            else:
                text = text.replace(",", ".")
        elif "." in text:
            if re.fullmatch(r"[+-]?\d{1,3}(?:\.\d{3})+", text):
                text = text.replace(".", "")
        try:
            number = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"Valor numérico inválido: {value!r}") from exc
        if negative:
            number = -number
    if not number.is_finite():
        raise ValueError("Valor numérico não finito")
    return number


def parse_date(
    value: Any,
    *,
    day_first: bool = True,
    warnings: list[str] | None = None,
) -> date | datetime | None:
    """Parse dates, including Excel serials, without guessing ambiguous text.

    ``09/10/2026`` returns ``None`` and emits a warning because both day/month
    orders are possible. Native Excel date cells are unambiguous and preserved.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return value
    if isinstance(value, bool):
        raise ValueError("Data booleana inválida")
    if isinstance(value, (int, float, Decimal)):
        try:
            serial = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError("Serial Excel inválido") from exc
        if not serial.is_finite() or serial < 1 or serial > 2958465:
            raise ValueError("Serial Excel fora do intervalo válido")
        parsed = from_excel(float(serial))
        if isinstance(parsed, time):
            raise ValueError("Serial Excel contém apenas horário")
        return parsed

    text = unicodedata.normalize("NFKC", str(value)).strip()
    iso = text.replace("Z", "+00:00")
    if re.match(r"^\d{4}-\d{1,2}-\d{1,2}", iso):
        try:
            if "T" in iso or " " in iso:
                return datetime.fromisoformat(iso)
            return date.fromisoformat(iso)
        except ValueError as exc:
            raise ValueError(f"Data inválida: {value!r}") from exc

    match = re.fullmatch(
        r"(\d{1,2})([/.-])(\d{1,2})\2(\d{4})(?:[,\s]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?",
        text,
    )
    if not match:
        raise ValueError(f"Formato de data não reconhecido: {value!r}")
    first, _, second, year, hour, minute, second_time = match.groups()
    a, b = int(first), int(second)
    if 1 <= a <= 12 and 1 <= b <= 12 and a != b:
        message = f"Data ambígua {text!r}: informe dia e mês sem ambiguidade."
        if warnings is not None:
            warnings.append(message)
        else:
            python_warnings.warn(message, UserWarning, stacklevel=2)
        return None
    if a > 12 and b <= 12:
        day, month = a, b
    elif b > 12 and a <= 12:
        day, month = b, a
    else:
        day, month = (a, b) if day_first else (b, a)
    try:
        parsed = date(int(year), month, day)
        if hour is not None:
            return datetime.combine(parsed, time(int(hour), int(minute), int(second_time or 0)))
        return parsed
    except ValueError as exc:
        raise ValueError(f"Data inválida: {value!r}") from exc
