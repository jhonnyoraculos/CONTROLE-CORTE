"""Confirmed material shortages, resolution, and possible source hints."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models import Audit, MaterialIssue, Order, ProductionEvent, Service, StatusHistory, now
from importers.normalizers import parse_decimal
from services.auth_service import require_role


_CLOSED_STATUSES = {"ENTREGUE", "RETIRADO", "CANCELADO"}


def _audit(
    session: Session, entity: str, entity_id: uuid.UUID, field: str,
    old: object, new: object, user_id: uuid.UUID | None, action: str,
) -> None:
    session.add(Audit(
        user_id=user_id, entity=entity, entity_id=str(entity_id), field=field,
        old_value=str(old) if old is not None else None,
        new_value=str(new) if new is not None else None,
        source="MATERIAL", action=action,
    ))


def _status(session: Session, order: Order, new_status: str, user_id: uuid.UUID | None) -> None:
    old_status = order.status_producao
    if old_status == new_status:
        return
    order.status_producao = new_status
    order.updated_by = user_id
    session.add(StatusHistory(
        pedido_id=order.id, old_status=old_status,
        new_status=new_status, user_id=user_id,
    ))
    _audit(session, "pedidos", order.id, "status_producao", old_status,
           new_status, user_id, "STATUS_CHANGED")


def _quantity(value: Decimal | str | int | float) -> Decimal:
    try:
        quantity = parse_decimal(value)
    except (ValueError, InvalidOperation) as exc:
        raise ValueError("Quantidade inválida.") from exc
    if quantity is None or quantity <= 0:
        raise ValueError("A quantidade deve ser maior que zero.")
    if quantity >= Decimal("10000000000000"):
        raise ValueError("Quantidade excede o limite permitido.")
    try:
        rounded = quantity.quantize(Decimal("0.001"))
    except InvalidOperation as exc:
        raise ValueError("Quantidade excede o limite permitido.") from exc
    if rounded != quantity:
        raise ValueError("A quantidade pode ter no máximo três casas decimais.")
    return rounded


def _required_text(value: str, label: str, max_length: int | None = None) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{label} é obrigatório.")
    if max_length is not None and len(text) > max_length:
        raise ValueError(f"{label} excede {max_length} caracteres.")
    return text


def register_issue(
    session: Session,
    order_id: uuid.UUID,
    *,
    produto: str,
    quantidade: Decimal | str | int | float,
    motivo: str,
    previsao_solucao: date | None,
    responsavel: str,
    user_id: uuid.UUID | None,
    role: str,
) -> MaterialIssue:
    """Confirm a shortage and pause the order until every issue is resolved."""
    require_role(role, "material")
    product = _required_text(produto, "Produto", 255)
    reason = _required_text(motivo, "Motivo")
    responsible = _required_text(responsavel, "Responsável", 180)
    amount = _quantity(quantidade)
    if isinstance(previsao_solucao, datetime):
        previsao_solucao = previsao_solucao.date()
    if previsao_solucao is not None and not isinstance(previsao_solucao, date):
        raise ValueError("Previsão de solução inválida.")

    order = session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        raise ValueError("Pedido não encontrado.")
    if order.status_producao in _CLOSED_STATUSES:
        raise ValueError("Pedido encerrado não pode receber falta de material.")

    open_count = session.scalar(select(func.count()).select_from(MaterialIssue).where(
        MaterialIssue.pedido_id == order.id,
        MaterialIssue.status_pendencia == "FALTA_MATERIAL",
    )) or 0
    if not open_count and order.status_producao != "AGUARDANDO_MATERIAL":
        old_return_status = order.status_antes_material
        order.status_antes_material = order.status_producao
        _audit(session, "pedidos", order.id, "status_antes_material",
               old_return_status, order.status_antes_material, user_id, "UPDATE")

    issue = MaterialIssue(
        pedido_id=order.id, produto=product, quantidade=amount, motivo=reason,
        previsao_solucao=previsao_solucao, responsavel=responsible,
        status_pendencia="FALTA_MATERIAL", registrado_em=now(), registrado_por=user_id,
    )
    session.add(issue)
    session.flush()
    _audit(session, "pendencias_material", issue.id, "status_pendencia", None,
           "FALTA_MATERIAL", user_id, "CREATE")
    if not order.falta_material:
        order.falta_material = True
        _audit(session, "pedidos", order.id, "falta_material", False, True, user_id, "UPDATE")
    _status(session, order, "AGUARDANDO_MATERIAL", user_id)
    session.add(ProductionEvent(
        pedido_id=order.id, type="REGISTRAR_FALTA_MATERIAL", at=now(), user_id=user_id,
        observation=f"{product}: {amount} · {reason}",
    ))
    return issue


def resolve_issue(
    session: Session,
    issue_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None,
    role: str,
    observacao: str | None = None,
) -> MaterialIssue:
    """Resolve one shortage; resume the order after its last open shortage."""
    require_role(role, "material")
    issue = session.scalar(select(MaterialIssue).where(MaterialIssue.id == issue_id).with_for_update())
    if issue is None:
        raise ValueError("Pendência de material não encontrada.")
    if issue.resolvido_em is not None or issue.status_pendencia != "FALTA_MATERIAL":
        raise ValueError("Esta pendência de material já foi resolvida.")
    order = session.scalar(select(Order).where(Order.id == issue.pedido_id).with_for_update())
    if order is None:
        raise ValueError("Pedido da pendência não encontrado.")
    note = (observacao or "").strip() or None
    issue.resolvido_em = now()
    issue.resolvido_por = user_id
    issue.resolucao_observacao = note
    issue.status_pendencia = "RESOLVIDA"
    _audit(session, "pendencias_material", issue.id, "status_pendencia",
           "FALTA_MATERIAL", "RESOLVIDA", user_id, "RESOLVE")
    _audit(session, "pendencias_material", issue.id, "resolvido_em",
           None, issue.resolvido_em, user_id, "RESOLVE")
    session.add(ProductionEvent(
        pedido_id=order.id, type="RESOLVER_FALTA_MATERIAL", at=issue.resolvido_em,
        user_id=user_id, observation=f"{issue.produto}: {note or 'material disponível'}",
    ))
    session.flush()
    open_count = session.scalar(select(func.count()).select_from(MaterialIssue).where(
        MaterialIssue.pedido_id == order.id,
        MaterialIssue.status_pendencia == "FALTA_MATERIAL",
    )) or 0
    if not open_count:
        if order.falta_material:
            order.falta_material = False
            _audit(session, "pedidos", order.id, "falta_material", True, False, user_id, "UPDATE")
        if order.status_producao == "AGUARDANDO_MATERIAL":
            restored = order.status_antes_material or (
                "PROGRAMADO" if order.programado_em else "AGUARDANDO_PROGRAMACAO"
            )
            _status(session, order, restored, user_id)
        if order.status_antes_material is not None:
            old_return_status = order.status_antes_material
            order.status_antes_material = None
            _audit(session, "pedidos", order.id, "status_antes_material",
                   old_return_status, None, user_id, "UPDATE")
    return issue


def possible_issue_candidates(session: Session, limit: int = 100) -> list[dict[str, object]]:
    """Show indicative source text for human confirmation, without changing status."""
    if limit <= 0:
        return []
    matches = session.execute(
        select(Order, Service).join(Service, Service.pedido_id == Order.id).where(
            Order.falta_material.is_(False),
            ~Order.material_issues.any(),
            Service.observacao_origem.ilike("%falta%"),
        ).order_by(Service.previsao_entrega.asc()).limit(limit * 4)
    ).all()
    candidates: list[dict[str, object]] = []
    seen_orders: set[uuid.UUID] = set()
    for order, service in matches:
        if order.id in seen_orders or not re.search(r"\bFALTA\b", service.observacao_origem or "", re.I):
            continue
        seen_orders.add(order.id)
        candidates.append({
            "order_id": order.id,
            "order_code": order.codigo_interno,
            "service_code": service.codigo_servico,
            "observation": service.observacao_origem,
            "status_pendencia": "POSSIVEL_FALTA_MATERIAL",
        })
        if len(candidates) == limit:
            break
    return candidates
