"""The imported identifiers and Brazilian values are not floating point math."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from importers.normalizers import normalize_identifier, parse_date, parse_decimal
from utils.formatters import date_br, datetime_br


def test_identifiers_preserve_leading_zeroes_and_expand_scientific_notation():
    assert [normalize_identifier(value) for value in
            ("173368", 173368, "173368.0", " 173368 ")] == ["173368"] * 4
    assert normalize_identifier("001234") == "001234"
    assert normalize_identifier("1.73368E+5") == "173368"
    assert normalize_identifier("173091 173167") == "173091 173167"
    with pytest.raises(ValueError):
        normalize_identifier(173368.5)


def test_brazilian_numbers_and_ambiguous_dates():
    assert parse_decimal("1.234,56") == Decimal("1234.56")
    assert parse_decimal(1234.56) == Decimal("1234.56")
    warnings = []
    assert parse_date("09/10/2026", warnings=warnings) is None
    assert warnings and "ambígua" in warnings[0]
    assert parse_date("21/09/2026") == date(2026, 9, 21)


def test_brazilian_date_display_converts_aware_timestamps():
    assert date_br(date(2026, 9, 21)) == "21/09/2026"
    assert datetime_br(datetime(2026, 9, 16, 16, 30, tzinfo=timezone.utc)) == "16/09/2026 13:30"
