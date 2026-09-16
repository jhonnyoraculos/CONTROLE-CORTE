"""Brazilian display formatting without changing stored numeric values."""

import os
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo


def decimal_br(value: Decimal | int | float | None, places: int = 3,
               trim: bool = True) -> str:
    if value is None:
        return "—"
    text = f"{Decimal(str(value)):,.{places}f}"
    whole, _, fraction = text.partition(".")
    whole = whole.replace(",", ".")
    fraction = fraction.rstrip("0") if trim else fraction
    return f"{whole},{fraction}" if fraction else whole


def money_br(value: Decimal | None) -> str:
    return "—" if value is None else f"R$ {decimal_br(value, 2, trim=False)}"


def date_br(value: date | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%d/%m/%Y")


def datetime_br(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is not None:
        value = value.astimezone(ZoneInfo(os.getenv("APP_TIMEZONE", "America/Sao_Paulo")))
    return value.strftime("%d/%m/%Y %H:%M")
