"""The main dashboard counts all orders and only manually set deliveries."""

from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db.models import Audit, Base, Order
from ui.overview import _overview_data


def test_overview_shows_order_progress_without_spreadsheet_delivery_dates(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'overview.sqlite').as_posix()}")
    Base.metadata.create_all(engine)
    today = date(2026, 9, 17)
    with Session(engine) as session, session.begin():
        orders = [
            Order(codigo_interno="A", codigo_interno_normalizado="A",
                  status_producao="AGUARDANDO_PROGRAMACAO",
                  previsao_entrega=today - timedelta(days=1)),
            Order(codigo_interno="B", codigo_interno_normalizado="B",
                  status_producao="EM_CORTE",
                  previsao_entrega=today + timedelta(days=1)),
            Order(codigo_interno="C", codigo_interno_normalizado="C",
                  status_producao="PRODUCAO_FINALIZADA"),
            Order(codigo_interno="D", codigo_interno_normalizado="D",
                  status_producao="AGUARDANDO_PROGRAMACAO",
                  previsao_entrega=today - timedelta(days=1)),
        ]
        session.add_all(orders)
        session.flush()
        for order in (orders[1], orders[3]):
            session.add(Audit(entity="pedidos", entity_id=str(order.id),
                              field="previsao_entrega", source="ROTA",
                              action="DELIVERY_DATE_SET"))
    with Session(engine) as session:
        data = _overview_data(session, today)
        assert (data["total"], data["awaiting"], data["active"], data["finished"]) == (4, 2, 1, 1)
        assert (data["due_soon"], data["overdue"]) == (1, 1)
        assert [order.codigo_interno for order in data["production"]] == ["B"]
        assert {order.codigo_interno for order in data["waiting"]} == {"A", "D"}
        assert [order.codigo_interno for order in data["upcoming"]] == ["B"]
        assert [order.codigo_interno for order in data["late"]] == ["D"]
