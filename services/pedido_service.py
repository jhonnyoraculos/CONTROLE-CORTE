"""Order totals, completeness checks and delivery date rules."""

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Audit, Order
from services.route_service import check_delivery_route


MEASURES = ("chapas", "cortes", "metros_lineares_corte", "pecas", "fita_aplicada", "usinagens")


def order_totals(order: Order) -> dict[str, Decimal]:
    return {
        name: sum((getattr(service, name) or Decimal(0) for service in order.services), Decimal(0))
        for name in MEASURES
    }


def completeness(order: Order) -> dict[str, bool]:
    return {
        "Servico importado": bool(order.services),
        "Carrinho importado": bool(order.carrinho),
        "Cliente identificado": bool(order.cliente_pdf),
        "Endereco identificado": bool(order.endereco),
        "Cidade identificada": bool(order.cidade),
        "Produtos identificados": bool(order.items),
        "Entrega definida": bool(order.previsao_entrega),
        "Producao programada": bool(order.programado_em),
    }


def set_delivery_date(session: Session, order: Order, delivery_date: date,
                      actor_id, role: str) -> None:
    if role == "CONSULTA":
        raise PermissionError("Este perfil nao pode definir data de entrega.")
    check = check_delivery_route(order.cidade, delivery_date)
    if not check.ok:
        raise ValueError(check.message)
    old = order.previsao_entrega
    already_confirmed = session.scalar(select(Audit.id).where(
        Audit.entity == "pedidos",
        Audit.entity_id == str(order.id),
        Audit.field == "previsao_entrega",
        Audit.source == "ROTA",
    ).limit(1)) is not None
    if old == delivery_date and already_confirmed:
        return
    order.previsao_entrega = delivery_date
    order.updated_by = actor_id
    session.add(Audit(user_id=actor_id, entity="pedidos", entity_id=str(order.id),
                      field="previsao_entrega", old_value=None if old is None else str(old),
                      new_value=str(delivery_date), source="ROTA",
                      action="DELIVERY_DATE_CONFIRMED" if old == delivery_date else "DELIVERY_DATE_SET"))


def clear_spreadsheet_delivery_dates(session: Session, actor_id, role: str) -> int:
    """Remove delivery dates that were filled by older service-spreadsheet imports."""
    if role == "CONSULTA":
        raise PermissionError("Este perfil nao pode limpar datas de entrega.")

    manual_ids = set(session.scalars(select(Audit.entity_id).where(
        Audit.entity == "pedidos",
        Audit.field == "previsao_entrega",
        Audit.source == "ROTA",
    )).all())
    orders = session.scalars(select(Order).where(
        Order.previsao_entrega.is_not(None),
    )).all()
    for order in orders:
        if str(order.id) in manual_ids:
            continue
        old = order.previsao_entrega
        order.previsao_entrega = None
        order.updated_by = actor_id
        session.add(Audit(user_id=actor_id, entity="pedidos", entity_id=str(order.id),
                          field="previsao_entrega",
                          old_value=None if old is None else str(old),
                          new_value=None, source="LIMPEZA_ROTA",
                          action="DELIVERY_DATE_CLEAR"))
    return sum(1 for order in orders if str(order.id) not in manual_ids)
