"""Production time based on the stated output of a 16-hour workday."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from typing import Iterable


WORKDAY_SECONDS = 16 * 60 * 60
CAPACITY_PER_DAY = {
    "cortes": Decimal("3200"),
    "furacoes": Decimal("5000"),
    "fita_metros": Decimal("1800"),
}
ESTIMATE_MODE = "SEQUENCIAL"


@dataclass(frozen=True)
class ProductionEstimate:
    cuts: Decimal
    drillings: Decimal
    edge_meters: Decimal
    cut_seconds: int
    drilling_seconds: int
    edge_seconds: int
    total_seconds: int
    mode: str = ESTIMATE_MODE

    @classmethod
    def from_snapshot(cls, payload: dict) -> "ProductionEstimate":
        return cls(
            cuts=Decimal(payload["cuts"]),
            drillings=Decimal(payload["drillings"]),
            edge_meters=Decimal(payload["edge_meters"]),
            cut_seconds=int(payload["cut_seconds"]),
            drilling_seconds=int(payload["drilling_seconds"]),
            edge_seconds=int(payload["edge_seconds"]),
            total_seconds=int(payload["total_seconds"]),
            mode=payload.get("mode", ESTIMATE_MODE),
        )

    def snapshot(self) -> dict:
        return {
            "version": 1,
            "mode": self.mode,
            "cuts": str(self.cuts),
            "drillings": str(self.drillings),
            "edge_meters": str(self.edge_meters),
            "cut_seconds": self.cut_seconds,
            "drilling_seconds": self.drilling_seconds,
            "edge_seconds": self.edge_seconds,
            "total_seconds": self.total_seconds,
            "workday_seconds": WORKDAY_SECONDS,
            "capacity_per_day": {key: str(value) for key, value in CAPACITY_PER_DAY.items()},
        }


def _seconds(quantity: Decimal, daily_capacity: Decimal) -> int:
    if quantity <= 0:
        return 0
    return int((quantity * Decimal(WORKDAY_SECONDS) / daily_capacity).to_integral_value(
        rounding=ROUND_CEILING))


def estimate_production(services: Iterable, mode: str = ESTIMATE_MODE) -> ProductionEstimate:
    if mode not in {"SEQUENCIAL", "PARALELO"}:
        raise ValueError("Modo de estimativa invalido.")
    services = list(services)
    cuts = sum((Decimal(str(item.cortes or 0)) for item in services), Decimal(0))
    drillings = sum((Decimal(str(item.usinagens or 0)) for item in services), Decimal(0))
    edge = sum((Decimal(str(item.fita_aplicada or 0)) for item in services), Decimal(0))
    cut_seconds = _seconds(cuts, CAPACITY_PER_DAY["cortes"])
    drilling_seconds = _seconds(drillings, CAPACITY_PER_DAY["furacoes"])
    edge_seconds = _seconds(edge, CAPACITY_PER_DAY["fita_metros"])
    stages = (cut_seconds, drilling_seconds, edge_seconds)
    total = sum(stages) if mode == "SEQUENCIAL" else max(stages)
    return ProductionEstimate(cuts, drillings, edge, cut_seconds, drilling_seconds,
                              edge_seconds, total, mode)


def duration_label(seconds: int) -> str:
    hours, rest = divmod(seconds, 3600)
    minutes, remaining_seconds = divmod(rest, 60)
    label = f"{hours}h {minutes:02d}min"
    return f"{label} {remaining_seconds:02d}s" if remaining_seconds else label


def workdays_label(seconds: int) -> str:
    days, remainder = divmod(seconds, WORKDAY_SECONDS)
    if not days:
        return "menos de 1 jornada de 16h"
    unit = "jornada" if days == 1 else "jornadas"
    if not remainder:
        return f"{days} {unit} de 16h"
    return f"{days} {unit} de 16h + {duration_label(remainder)}"
