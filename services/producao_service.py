"""Production planning, events, durations and capacity checks."""

import uuid
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from config.constants import PROCESS_EVENTS
from db.models import Audit, Capacity, Order, ProductionEvent, Service, StatusHistory, now
from services.auth_service import require_role
from services.estimativa_service import ProductionEstimate, estimate_production
from services.pedido_service import order_totals
from services.status_flow import get_flow, get_statuses
from utils.logging import log_event


METRIC_FIELDS = {"chapas": "chapas", "cortes": "cortes", "metros_corte": "metros_lineares_corte",
                 "fita": "fita_aplicada", "usinagens": "usinagens"}


def _save_estimate(session: Session, order: Order, user_id, start_at: datetime) -> None:
    estimate = estimate_production(order.services)
    if not estimate.total_seconds:
        return
    payload = estimate.snapshot()
    payload["start_at"] = start_at.isoformat()
    session.add(Audit(user_id=user_id, entity="pedidos", entity_id=str(order.id),
                      field="estimativa_producao", old_value=None,
                      new_value=json.dumps(payload, ensure_ascii=False),
                      source="PRODUCAO", action="ESTIMATE_CREATED"))


def saved_estimate(session: Session, order_id) -> ProductionEstimate | None:
    value = session.scalar(select(Audit.new_value).where(
        Audit.entity == "pedidos", Audit.entity_id == str(order_id),
        Audit.field == "estimativa_producao", Audit.source == "PRODUCAO",
    ).order_by(Audit.at.desc()).limit(1))
    if not value:
        return None
    try:
        return ProductionEstimate.from_snapshot(json.loads(value))
    except (KeyError, TypeError, ValueError):
        return None


def _status(session: Session, order: Order, new: str, user_id, source: str) -> None:
    old = order.status_producao
    if old == new:
        return
    order.status_producao = new
    order.updated_by = user_id
    session.add(StatusHistory(pedido_id=order.id, old_status=old,
                              new_status=new, user_id=user_id))
    session.add(Audit(user_id=user_id, entity="pedidos", entity_id=str(order.id),
                      field="status_producao", old_value=old, new_value=new,
                      source=source, action="STATUS_CHANGED"))


def daily_capacity(session: Session, day: date, exclude_order_id=None) -> list[dict]:
    capacities = session.scalars(select(Capacity).where(Capacity.day == day)).all()
    orders = session.scalars(select(Order).where(Order.programado_em == day).options(
        selectinload(Order.services))).all()
    result = []
    for capacity in capacities:
        field = METRIC_FIELDS.get(capacity.metric)
        if not field:
            continue
        used = sum((order_totals(order)[field] for order in orders
                    if order.id != exclude_order_id and
                    (capacity.machine_id is None or capacity.machine_id == order.maquina_id) and
                    (capacity.shift == "GERAL" or capacity.shift == order.turno)), Decimal(0))
        result.append({"process": capacity.process, "metric": capacity.metric,
                       "used": used, "limit": capacity.limit,
                       "machine_id": capacity.machine_id, "shift": capacity.shift})
    return result


def preview_plan(session: Session, order: Order, day: date, machine_id=None,
                 shift: str = "GERAL") -> list[dict]:
    rows = daily_capacity(session, day, exclude_order_id=order.id)
    totals = order_totals(order)
    for row in rows:
        applies = (row["machine_id"] is None or row["machine_id"] == machine_id) and (
            row["shift"] == "GERAL" or row["shift"] == shift)
        row["projected"] = row["used"] + (totals[METRIC_FIELDS[row["metric"]]] if applies else Decimal(0))
        row["over"] = row["projected"] > row["limit"]
    return rows


def plan_order(session: Session, order: Order, day: date, shift: str,
               machine_id: uuid.UUID | None, priority: str, user_id,
               role: str, allow_over_capacity: bool = False) -> list[dict]:
    require_role(role, "plan")
    if allow_over_capacity and role not in {"ADMIN", "GESTOR"}:
        raise PermissionError("Este perfil não pode autorizar excesso de capacidade.")
    if priority not in {"BAIXA", "NORMAL", "ALTA", "URGENTE"}:
        raise ValueError("Prioridade inválida.")
    if order.status_producao in {"CANCELADO", "ENTREGUE", "RETIRADO"}:
        raise ValueError("Pedido encerrado não pode ser programado.")
    preview = preview_plan(session, order, day, machine_id, shift)
    if any(row["over"] for row in preview) and not allow_over_capacity:
        raise ValueError("A programação ultrapassará a capacidade configurada.")
    for key, value in {"programado_em": day, "turno": shift,
                       "maquina_id": machine_id, "prioridade": priority}.items():
        old = getattr(order, key)
        if old != value:
            setattr(order, key, value)
            session.add(Audit(user_id=user_id, entity="pedidos", entity_id=str(order.id),
                              field=key, old_value=str(old) if old is not None else None,
                              new_value=str(value) if value is not None else None,
                              source="MANUAL", action="PLAN_CHANGED"))
    _status(session, order, "PROGRAMADO", user_id, "MANUAL")
    log_event("PLAN_CHANGED", user_id=user_id, order_id=order.id)
    return preview


def record_event(session: Session, order: Order, event_type: str, user_id,
                 role: str, machine_id=None, observation: str | None = None) -> ProductionEvent:
    require_role(role, "produce")
    if event_type not in PROCESS_EVENTS:
        raise ValueError("Evento de produção inválido.")
    flow = get_flow(session)
    if order.status_producao not in flow[event_type]["from"]:
        raise ValueError(f"{event_type} não é permitido no status {order.status_producao}.")
    if order.falta_material and event_type == "INICIAR_CORTE":
        raise ValueError("Resolva a falta de material antes de iniciar o corte.")
    event = ProductionEvent(pedido_id=order.id, type=event_type, at=now(), user_id=user_id,
                            machine_id=machine_id or order.maquina_id, observation=observation)
    session.add(event)
    if event_type == "INICIAR_CORTE":
        _save_estimate(session, order, user_id, event.at)
    _status(session, order, flow[event_type]["to"], user_id, "PRODUCAO")
    log_event("PRODUCTION_FINISHED" if event_type == "FINALIZAR_PRODUCAO" else
              "PRODUCTION_STARTED" if event_type == "INICIAR_CORTE" else
              "STATUS_CHANGED", user_id=user_id, order_id=order.id)
    return event


ACTIVE_PRODUCTION_STATUSES = {"EM_CORTE", "AGUARDANDO_FITAGEM", "EM_FITAGEM",
                              "AGUARDANDO_USINAGEM", "EM_USINAGEM"}
CLOSED_PRODUCTION_STATUSES = {"PRODUCAO_FINALIZADA", "AGUARDANDO_CARREGAMENTO",
                              "LIBERADO_CARREGAMENTO", "ENTREGUE", "RETIRADO", "CANCELADO"}


@dataclass(frozen=True)
class ProductionClock:
    start_at: datetime
    estimated_seconds: int
    elapsed_seconds: int
    is_due: bool


def production_clock(session: Session, order: Order,
                     current: datetime | None = None) -> ProductionClock | None:
    if order.status_producao not in ACTIVE_PRODUCTION_STATUSES:
        return None
    start = session.scalar(select(ProductionEvent).where(
        ProductionEvent.pedido_id == order.id,
        ProductionEvent.type == "INICIAR_CORTE",
    ).order_by(ProductionEvent.at.desc()).limit(1))
    if start is None:
        return None
    start_at = start.at if start.at.tzinfo else start.at.replace(tzinfo=timezone.utc)
    current_at = current or now()
    if current_at.tzinfo is None:
        current_at = current_at.replace(tzinfo=timezone.utc)
    elapsed = max(0, int((current_at - start_at).total_seconds()))
    estimate = saved_estimate(session, order.id) or estimate_production(order.services)
    seconds = estimate.total_seconds
    return ProductionClock(start_at, seconds, elapsed, bool(seconds and elapsed >= seconds))


def start_production(session: Session, order: Order, user_id, role: str,
                     started_at: datetime | None = None,
                     responsible: str = "") -> ProductionEvent:
    """Record the operator's actual start time and move the order into production."""
    require_role(role, "produce")
    if not responsible.strip():
        raise ValueError("Informe quem esta registrando o inicio da producao.")
    if order.status_producao in ACTIVE_PRODUCTION_STATUSES | CLOSED_PRODUCTION_STATUSES:
        raise ValueError("Este pedido ja esta em producao ou foi encerrado.")
    if order.falta_material:
        raise ValueError("Resolva a falta de material antes de iniciar a producao.")
    start = started_at or now()
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("Informe data e hora com fuso horario.")
    start = start.astimezone(timezone.utc)
    if start > now():
        raise ValueError("O inicio da producao nao pode estar no futuro.")
    event = ProductionEvent(pedido_id=order.id, type="INICIAR_CORTE", at=start,
                            user_id=user_id, machine_id=order.maquina_id,
                            observation=f"Responsavel: {responsible.strip()}" if responsible.strip() else None)
    session.add(event)
    _save_estimate(session, order, user_id, start)
    _status(session, order, "EM_CORTE", user_id, "PRODUCAO")
    log_event("PRODUCTION_STARTED", user_id=user_id, order_id=order.id)
    return event


def finish_production(session: Session, order: Order, user_id, role: str,
                      responsible: str = "") -> ProductionEvent:
    """Close an active production order at the current time."""
    require_role(role, "produce")
    if not responsible.strip():
        raise ValueError("Informe quem esta encerrando a producao.")
    if order.status_producao not in ACTIVE_PRODUCTION_STATUSES:
        raise ValueError("Este pedido nao esta em producao.")
    start = session.scalar(select(ProductionEvent).where(
        ProductionEvent.pedido_id == order.id,
        ProductionEvent.type == "INICIAR_CORTE",
    ).order_by(ProductionEvent.at.desc()).limit(1))
    if start is None:
        raise ValueError("Registre o inicio da producao antes de encerrar.")
    finished_at = now()
    started_at = start.at if start.at.tzinfo else start.at.replace(tzinfo=timezone.utc)
    if finished_at < started_at:
        raise ValueError("O fim da producao nao pode ser anterior ao inicio.")
    event = ProductionEvent(pedido_id=order.id, type="FINALIZAR_PRODUCAO", at=finished_at,
                            user_id=user_id, machine_id=order.maquina_id,
                            observation=f"Responsavel: {responsible.strip()}" if responsible.strip() else None)
    session.add(event)
    _status(session, order, "PRODUCAO_FINALIZADA", user_id, "PRODUCAO")
    log_event("PRODUCTION_FINISHED", user_id=user_id, order_id=order.id)
    return event


def set_status(session: Session, order: Order, new_status: str, user_id,
               role: str, reason: str = "") -> None:
    """Manager adjustment for logistics, cancellation, or exceptional states."""
    if role not in {"ADMIN", "GESTOR"}:
        raise PermissionError("Este perfil não pode alterar o status manualmente.")
    if new_status not in get_statuses(session):
        raise ValueError("Status de produção inválido.")
    if order.falta_material and new_status not in {"AGUARDANDO_MATERIAL", "CANCELADO"}:
        raise ValueError("Resolva a falta de material antes de alterar o status.")
    if new_status == "CANCELADO" and not reason.strip():
        raise ValueError("Informe o motivo do cancelamento.")
    old = order.status_producao
    _status(session, order, new_status, user_id, "MANUAL")
    if old != new_status:
        session.add(ProductionEvent(pedido_id=order.id, type="STATUS_MANUAL", at=now(),
                                    user_id=user_id, machine_id=order.maquina_id,
                                    observation=reason.strip() or f"{old} → {new_status}"))
        log_event("STATUS_CHANGED", user_id=user_id, order_id=order.id)


def durations(events: list[ProductionEvent]) -> dict[str, str]:
    def aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    by_type = {event.type: aware(event.at) for event in sorted(events, key=lambda item: aware(item.at))}
    pairs = {"corte": ("INICIAR_CORTE", "FINALIZAR_CORTE"),
             "fitagem": ("INICIAR_FITAGEM", "FINALIZAR_FITAGEM"),
             "usinagem": ("INICIAR_USINAGEM", "FINALIZAR_PRODUCAO"),
             "producao": ("INICIAR_CORTE", "FINALIZAR_PRODUCAO")}
    result = {}
    for label, (start, end) in pairs.items():
        if start in by_type and end in by_type and by_type[end] >= by_type[start]:
            minutes = int((by_type[end] - by_type[start]).total_seconds() // 60)
            result[label] = f"{minutes // 60:02d}h {minutes % 60:02d}min"
    return result
