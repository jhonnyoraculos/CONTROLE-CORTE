"""Filtered order queries for Streamlit pages."""

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, selectinload

from db.models import Order, Service
from importers.normalizers import normalize_identifier


def find_order(session: Session, code: str) -> Order | None:
    return session.scalar(select(Order).where(Order.codigo_interno_normalizado == normalize_identifier(code)))


def search_orders(session: Session, term: str = "", status: str | None = None,
                  limit: int = 50, offset: int = 0, seller: str = "",
                  line: str = "", machine_id=None, priority: str | None = None,
                  sort: str = "newest") -> tuple[list[Order], int]:
    stmt = select(Order)
    if term.strip():
        pattern = f"%{term.strip()}%"
        normalized = normalize_identifier(term)
        stmt = stmt.where(or_(Order.codigo_interno.ilike(pattern),
                              Order.codigo_interno_normalizado == normalized,
                              Order.carrinho.ilike(pattern), Order.carrinho == normalized,
                              Order.cliente_pdf.ilike(pattern), Order.cliente_documento.ilike(pattern),
                              Order.codigo_cliente_pdf.ilike(pattern), Order.cidade.ilike(pattern),
                              Order.vendedor_pdf.ilike(pattern),
                              Order.id.in_(select(Service.pedido_id).where(
                                  or_(Service.codigo_servico.ilike(pattern),
                                      Service.codigo_servico_normalizado == normalized,
                                      Service.cliente_origem.ilike(pattern),
                                      Service.cliente_final_origem.ilike(pattern),
                                      Service.vendedor_origem.ilike(pattern))))))
    if status:
        stmt = stmt.where(Order.status_producao == status)
    if seller.strip():
        pattern = f"%{seller.strip()}%"
        stmt = stmt.where(or_(Order.vendedor_pdf.ilike(pattern), Order.services.any(
            Service.vendedor_origem.ilike(pattern))))
    if line.strip():
        stmt = stmt.where(Order.services.any(Service.linha_producao.ilike(f"%{line.strip()}%")))
    if machine_id:
        stmt = stmt.where(Order.maquina_id == machine_id)
    if priority:
        stmt = stmt.where(Order.prioridade == priority)
    count = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    if sort == "due":
        ordering = (Order.previsao_entrega.asc().nulls_last(), Order.codigo_interno)
    elif sort == "priority":
        rank = case((Order.prioridade == "URGENTE", 0),
                    (Order.prioridade == "ALTA", 1),
                    (Order.prioridade == "NORMAL", 2),
                    else_=3)
        ordering = (rank, Order.previsao_entrega.asc().nulls_last(), Order.codigo_interno)
    else:
        ordering = (Order.created_at.desc(), Order.codigo_interno)
    rows = session.scalars(stmt.order_by(*ordering).limit(limit).offset(offset)).all()
    return rows, count


def order_detail(session: Session, order_id) -> Order | None:
    return session.scalar(select(Order).where(Order.id == order_id).options(
        selectinload(Order.services), selectinload(Order.items)))
