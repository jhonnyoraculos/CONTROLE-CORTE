"""Main dashboard for current order and production progress."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import streamlit as st
from sqlalchemy import func, select

from db.models import Audit, Order
from services.producao_service import ACTIVE_PRODUCTION_STATUSES, CLOSED_PRODUCTION_STATUSES
from ui.operation_grid import _rows
from ui.overview_visuals import render_visuals
from ui.styles import page_header


def _manual_delivery_ids(session) -> list[UUID]:
    values = session.scalars(select(Audit.entity_id).where(
        Audit.entity == "pedidos",
        Audit.field == "previsao_entrega",
        Audit.source == "ROTA",
    ).distinct()).all()
    ids = set()
    for value in values:
        try:
            ids.add(UUID(value))
        except (TypeError, ValueError):
            continue
    return list(ids)


def _overview_data(session, today):
    status_counts = dict(session.execute(select(
        Order.status_producao, func.count(Order.id),
    ).group_by(Order.status_producao)).all())
    total = sum(status_counts.values())
    active = sum(status_counts.get(status, 0) for status in ACTIVE_PRODUCTION_STATUSES)
    closed = sum(status_counts.get(status, 0) for status in CLOSED_PRODUCTION_STATUSES)
    delivered = status_counts.get("ENTREGUE", 0) + status_counts.get("RETIRADO", 0)
    finished = closed - delivered - status_counts.get("CANCELADO", 0)
    awaiting = total - active - closed
    shortages = session.scalar(select(func.count(Order.id)).where(
        Order.falta_material.is_(True),
        Order.status_producao.not_in(CLOSED_PRODUCTION_STATUSES),
    )) or 0
    missing_cart = session.scalar(select(func.count(Order.id)).where(
        Order.carrinho.is_(None),
        Order.status_producao.not_in(CLOSED_PRODUCTION_STATUSES),
    )) or 0

    manual_ids = _manual_delivery_ids(session)
    due_soon = overdue = 0
    upcoming = []
    late = []
    if manual_ids:
        delivery_base = (Order.id.in_(manual_ids), Order.previsao_entrega.is_not(None))
        due_soon = session.scalar(select(func.count(Order.id)).where(
            *delivery_base, Order.previsao_entrega.between(today, today + timedelta(days=7)),
            Order.status_producao.not_in(("ENTREGUE", "RETIRADO", "CANCELADO")),
        )) or 0
        overdue = session.scalar(select(func.count(Order.id)).where(
            *delivery_base, Order.previsao_entrega < today,
            Order.status_producao.not_in(("ENTREGUE", "RETIRADO", "CANCELADO")),
        )) or 0
        upcoming = session.scalars(select(Order).where(
            *delivery_base, Order.previsao_entrega >= today,
            Order.status_producao.not_in(("ENTREGUE", "RETIRADO", "CANCELADO")),
        ).order_by(Order.previsao_entrega, Order.codigo_interno).limit(10)).all()
        late = session.scalars(select(Order).where(
            *delivery_base, Order.previsao_entrega < today,
            Order.status_producao.not_in(("ENTREGUE", "RETIRADO", "CANCELADO")),
        ).order_by(Order.previsao_entrega, Order.codigo_interno).limit(10)).all()

    production = session.scalars(select(Order).where(
        Order.status_producao.in_(ACTIVE_PRODUCTION_STATUSES),
    ).order_by(Order.updated_at.desc(), Order.codigo_interno).limit(10)).all()
    waiting = session.scalars(select(Order).where(
        Order.status_producao.not_in(ACTIVE_PRODUCTION_STATUSES | CLOSED_PRODUCTION_STATUSES),
    ).order_by(Order.created_at.desc(), Order.codigo_interno).limit(10)).all()
    material = session.scalars(select(Order).where(
        Order.falta_material.is_(True),
        Order.status_producao.not_in(CLOSED_PRODUCTION_STATUSES),
    ).order_by(Order.updated_at.desc(), Order.codigo_interno).limit(10)).all()
    recent = session.scalars(select(Order).order_by(
        Order.created_at.desc(), Order.codigo_interno,
    ).limit(12)).all()
    return {
        "total": total, "awaiting": awaiting, "active": active,
        "finished": finished, "delivered": delivered, "shortages": shortages,
        "missing_cart": missing_cart, "due_soon": due_soon, "overdue": overdue,
        "production": production, "waiting": waiting, "material": material,
        "recent": recent, "upcoming": upcoming, "late": late,
    }


def render(factory, user):
    local_now = datetime.now(ZoneInfo(os.getenv("APP_TIMEZONE", "America/Sao_Paulo")))
    today = local_now.date()
    page_header(
        "Dashboard",
        "Pedidos, produção e entregas em um só lugar.",
        "Visão geral",
        "Sistema ativo",
        "D",
    )
    st.caption(f"Atualizado em {local_now:%d/%m/%Y às %H:%M}")

    with factory() as session:
        data = _overview_data(session, today)
        production_rows = _rows(session, data["production"], include_meta=True)

    render_visuals(data, production_rows, local_now)
