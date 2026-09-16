"""Query checks for operational dashboards and reports on local SQLite."""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db.models import Base, Capacity, Machine, Order, ProductionEvent, Service
from ui.dashboard import _metrics
from ui.reports import (ReportFilters, average_times, capacity_page, operational_summary,
                        order_page, production_trend, volume_page)


def test_report_queries_count_orders_once_and_use_real_events(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'reports.sqlite3'}")
    Base.metadata.create_all(engine)
    today = date.today()
    with Session(engine) as session, session.begin():
        machine = Machine(name="Serra 1", kind="CORTE")
        session.add(machine)
        session.flush()
        orders = [
            Order(codigo_interno="100", codigo_interno_normalizado="100",
                  cliente_pdf="Cliente A", vendedor_pdf="Ana", cidade="Divinópolis",
                  previsao_entrega=today - timedelta(days=1), programado_em=today,
                  maquina_id=machine.id, status_producao="PRODUCAO_FINALIZADA"),
            Order(codigo_interno="101", codigo_interno_normalizado="101",
                  cliente_pdf="Cliente B", vendedor_pdf="Beto", cidade="Divinópolis",
                  previsao_entrega=today - timedelta(days=1), falta_material=True,
                  status_producao="AGUARDANDO_MATERIAL"),
            Order(codigo_interno="102", codigo_interno_normalizado="102",
                  cliente_pdf="Cliente C", previsao_entrega=today - timedelta(days=1),
                  status_producao="ENTREGUE"),
        ]
        session.add_all(orders)
        session.flush()
        session.add_all([
            Service(pedido_id=orders[0].id, codigo_servico="S1",
                    codigo_servico_normalizado="S1", chapas=2, cortes=10),
            Service(pedido_id=orders[0].id, codigo_servico="S2",
                    codigo_servico_normalizado="S2", chapas=1, cortes=5),
            Service(pedido_id=orders[1].id, codigo_servico="S3",
                    codigo_servico_normalizado="S3", chapas=4, cortes=20),
        ])
        at = datetime.combine(today, time(13), timezone.utc)
        session.add_all([
            ProductionEvent(pedido_id=orders[0].id, type="INICIAR_CORTE", at=at),
            ProductionEvent(pedido_id=orders[0].id, type="FINALIZAR_CORTE",
                            at=at + timedelta(hours=1)),
            ProductionEvent(pedido_id=orders[0].id, type="INICIAR_FITAGEM",
                            at=at + timedelta(hours=1, minutes=15)),
                ProductionEvent(pedido_id=orders[0].id, type="FINALIZAR_FITAGEM",
                                at=at + timedelta(hours=2, minutes=15)),
                ProductionEvent(pedido_id=orders[0].id, type="INICIAR_USINAGEM",
                                at=at + timedelta(hours=2, minutes=30)),
                ProductionEvent(pedido_id=orders[0].id, type="FINALIZAR_PRODUCAO",
                            at=at + timedelta(hours=3)),
        ])
        session.add(Capacity(day=today, process="CORTE", metric="cortes",
                             limit=Decimal("10"), machine_id=machine.id, shift="GERAL"))

    filters = ReportFilters(today - timedelta(days=1), today + timedelta(days=1))
    with Session(engine) as session:
        summary = operational_summary(session, filters)
        assert summary == {"total": 3, "finalizados": 2, "abertos": 1,
                           "atrasados": 2, "falta_material": 1}
        metrics = _metrics(session, today)
        assert metrics["em_producao"] == 0
        assert metrics["finalizados_hoje"] == 1
        assert metrics["chapas_hoje"] == Decimal("3")
        assert metrics["cortes_hoje"] == Decimal("15")
        total, overdue = order_page(session, filters, "Atrasados", 1, 1)
        assert total == 2 and len(overdue) == 1
        total, machine_rows = volume_page(session, filters, "Máquina", 1, 10)
        assert total == 2
        serra = next(row for row in machine_rows if row["Máquina"] == "Serra 1")
        assert serra["Pedidos"] == 1
        assert serra["Chapas"] == Decimal("3")
        assert serra["Cortes"] == Decimal("15")
        assert production_trend(session, filters, "Diária") == [
            {"Período": today.isoformat(), "Pedidos finalizados": 1,
             "Chapas": Decimal("3"), "Cortes": Decimal("15"), "Peças": Decimal("0")}
        ]
        assert len(production_trend(session, filters, "Semanal")) == 1
        assert len(production_trend(session, filters, "Mensal")) == 1
        durations = average_times(session, filters)
        assert [row["Tempo médio (h)"] for row in durations] == [1.0, 1.0, 0.5, 3.0]
        total, capacity = capacity_page(session, filters, 1, 10)
        assert total == 1
        assert capacity[0]["Carga"] == Decimal("15")
        assert capacity[0]["Excedida"] == "Sim"
    engine.dispose()
