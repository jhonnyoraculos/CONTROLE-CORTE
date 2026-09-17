"""Production rates supplied by the shop floor for a 16-hour workday."""

from decimal import Decimal
from types import SimpleNamespace

from services.estimativa_service import (
    ProductionEstimate, duration_label, estimate_production, workdays_label,
)


def test_exact_daily_capacity_and_partial_workload():
    full = estimate_production([SimpleNamespace(
        cortes=Decimal("3200"), usinagens=Decimal("5000"),
        fita_aplicada=Decimal("1800"),
    )])
    assert (full.cut_seconds, full.drilling_seconds, full.edge_seconds) == (57600,) * 3
    assert full.total_seconds == 172800
    assert duration_label(full.total_seconds) == "48h 00min"
    assert workdays_label(full.total_seconds) == "3 jornadas de 16h"
    assert ProductionEstimate.from_snapshot(full.snapshot()) == full

    partial = estimate_production([SimpleNamespace(
        cortes=Decimal("200"), usinagens=Decimal("1000"),
        fita_aplicada=Decimal("112.5"),
    )])
    assert (partial.cut_seconds, partial.drilling_seconds, partial.edge_seconds) == (
        3600, 11520, 3600,
    )
    assert partial.total_seconds == 18720
    assert duration_label(partial.total_seconds) == "5h 12min"
