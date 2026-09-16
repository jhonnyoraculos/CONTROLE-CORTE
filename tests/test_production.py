"""Planning limits and guarded production transitions."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from db.models import Audit, Base, Capacity, Order, ProductionEvent, Service
from db.repositories.orders import search_orders
from services.producao_service import durations, plan_order, preview_plan, record_event, set_status


def test_capacity_and_production_history(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'production.sqlite').as_posix()}")
    Base.metadata.create_all(engine)
    day = date(2026, 9, 21)
    with Session(engine) as session, session.begin():
        order = Order(codigo_interno="173368", codigo_interno_normalizado="173368",
                      status_dados="DADOS_COMPLETOS", status_producao="AGUARDANDO_PROGRAMACAO")
        session.add(order)
        session.flush()
        session.add(Service(pedido_id=order.id, codigo_servico="20533164",
                            codigo_servico_normalizado="20533164", chapas=Decimal("11"), raw_data={}))
        session.add(Capacity(day=day, process="CORTE", metric="chapas",
                             limit=Decimal("10"), shift="GERAL"))
        session.flush()
        assert preview_plan(session, order, day)[0]["over"]
        with pytest.raises(ValueError, match="capacidade"):
            plan_order(session, order, day, "GERAL", None, "NORMAL", None, "GESTOR")
        with pytest.raises(PermissionError):
            plan_order(session, order, day, "GERAL", None, "NORMAL", None,
                       "PLANEJAMENTO", allow_over_capacity=True)
        assert order.status_producao == "AGUARDANDO_PROGRAMACAO"
        plan_order(session, order, day, "GERAL", None, "URGENTE", None, "GESTOR",
                   allow_over_capacity=True)
        with pytest.raises(PermissionError):
            record_event(session, order, "INICIAR_CORTE", None, "CONSULTA")
        for action in ("INICIAR_CORTE", "FINALIZAR_CORTE", "INICIAR_FITAGEM",
                       "FINALIZAR_FITAGEM", "INICIAR_USINAGEM", "FINALIZAR_PRODUCAO"):
            record_event(session, order, action, None, "OPERADOR")
        assert order.status_producao == "PRODUCAO_FINALIZADA"
        with pytest.raises(PermissionError):
            set_status(session, order, "ENTREGUE", None, "OPERADOR")
        with pytest.raises(ValueError, match="motivo"):
            set_status(session, order, "CANCELADO", None, "GESTOR")
        set_status(session, order, "AGUARDANDO_CARREGAMENTO", None, "GESTOR")
        assert order.status_producao == "AGUARDANDO_CARREGAMENTO"
    with Session(engine) as session:
        events = session.scalars(select(ProductionEvent).order_by(ProductionEvent.at)).all()
        assert len(events) == 7
        assert session.query(Audit).filter(Audit.action == "STATUS_CHANGED").count() >= 6
    start = datetime(2026, 9, 21, 8, 0, tzinfo=timezone.utc)
    events[0].at = start
    events[1].at = start + timedelta(minutes=90)
    next(event for event in events if event.type == "FINALIZAR_PRODUCAO").at = start + timedelta(minutes=180)
    assert durations(events)["corte"] == "01h 30min"
    assert durations(events)["producao"] == "03h 00min"


def test_queue_priority_sort_and_pagination(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'queue.sqlite').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        for code, priority, due in (
            ("N1", "NORMAL", date(2026, 9, 19)),
            ("U1", "URGENTE", date(2026, 9, 21)),
            ("A1", "ALTA", date(2026, 9, 18)),
            ("U2", "URGENTE", date(2026, 9, 17)),
        ):
            session.add(Order(codigo_interno=code, codigo_interno_normalizado=code,
                              prioridade=priority, previsao_entrega=due))
    with Session(engine) as session:
        first, total = search_orders(session, sort="priority", limit=2)
        second, _ = search_orders(session, sort="priority", limit=2, offset=2)
        assert total == 4
        assert [order.codigo_interno for order in first] == ["U2", "U1"]
        assert [order.codigo_interno for order in second] == ["A1", "N1"]
