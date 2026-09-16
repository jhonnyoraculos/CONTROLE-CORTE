"""Material shortage lifecycle and authorization checks."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from db.models import Audit, Base, MaterialIssue, Order, ProductionEvent, Service, StatusHistory
from services.material_service import possible_issue_candidates, register_issue, resolve_issue
from services.producao_service import record_event


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _order(session: Session) -> Order:
    order = Order(
        codigo_interno="173368", codigo_interno_normalizado="173368",
        status_producao="PROGRAMADO", programado_em=date(2026, 9, 18),
    )
    session.add(order)
    session.flush()
    session.add(Service(
        pedido_id=order.id, codigo_servico="20533164",
        codigo_servico_normalizado="20533164", raw_data={},
        observacao_origem="PRODUÇÃO FALTA 03 CHAPAS 8954",
    ))
    session.flush()
    return order


def test_operator_confirms_and_resolves_multiple_issues(session: Session) -> None:
    order = _order(session)
    hints = possible_issue_candidates(session)
    assert len(hints) == 1
    assert hints[0]["status_pendencia"] == "POSSIVEL_FALTA_MATERIAL"
    assert order.falta_material is False

    with pytest.raises(PermissionError):
        register_issue(
            session, order.id, produto="MDF 15 mm", quantidade="3", motivo="Sem estoque",
            previsao_solucao=date(2026, 9, 19), responsavel="Compras",
            user_id=None, role="CONSULTA",
        )
    assert session.scalar(select(func.count()).select_from(MaterialIssue)) == 0

    first = register_issue(
        session, order.id, produto="MDF 15 mm", quantidade="3", motivo="Sem estoque",
        previsao_solucao=date(2026, 9, 19), responsavel="Compras",
        user_id=None, role="OPERADOR",
    )
    second = register_issue(
        session, order.id, produto="Fita de borda", quantidade="2,5",
        motivo="Entrega do fornecedor pendente", previsao_solucao=None,
        responsavel="Almoxarifado", user_id=None, role="GESTOR",
    )
    session.flush()
    assert first.quantidade == Decimal("3.000")
    assert second.quantidade == Decimal("2.500")
    assert order.status_producao == "AGUARDANDO_MATERIAL"
    assert order.status_antes_material == "PROGRAMADO"
    assert order.falta_material is True
    assert possible_issue_candidates(session) == []
    with pytest.raises(ValueError, match="Resolva a falta"):
        record_event(session, order, "INICIAR_CORTE", None, "OPERADOR")

    resolve_issue(session, first.id, user_id=None, role="OPERADOR", observacao="MDF recebido")
    assert order.falta_material is True
    assert order.status_producao == "AGUARDANDO_MATERIAL"
    assert first.status_pendencia == "RESOLVIDA"
    with pytest.raises(ValueError, match="já foi resolvida"):
        resolve_issue(session, first.id, user_id=None, role="OPERADOR")

    resolve_issue(session, second.id, user_id=None, role="GESTOR")
    session.flush()
    assert order.falta_material is False
    assert order.status_producao == "PROGRAMADO"
    assert order.status_antes_material is None
    assert second.resolvido_em is not None
    assert second.status_pendencia == "RESOLVIDA"
    assert session.scalar(select(func.count()).select_from(StatusHistory)) == 2
    assert session.scalar(select(func.count()).select_from(ProductionEvent)) == 4
    assert session.scalar(select(func.count()).select_from(Audit).where(
        Audit.entity == "pendencias_material"
    )) >= 4


def test_shortage_validation_and_closed_order(session: Session) -> None:
    order = _order(session)
    for amount in ("0", "-1", "1,2345", "abc"):
        with pytest.raises(ValueError):
            register_issue(
                session, order.id, produto="MDF", quantidade=amount, motivo="Sem estoque",
                previsao_solucao=None, responsavel="Compras", user_id=None, role="ADMIN",
            )
    order.status_producao = "ENTREGUE"
    with pytest.raises(ValueError, match="encerrado"):
        register_issue(
            session, order.id, produto="MDF", quantidade="1", motivo="Sem estoque",
            previsao_solucao=None, responsavel="Compras", user_id=None, role="ADMIN",
        )
    assert session.scalar(select(func.count()).select_from(MaterialIssue)) == 0
