"""Compact spreadsheet view and manual production timing in Operacao."""

from __future__ import annotations

import os
import json
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import streamlit as st
from sqlalchemy import func, or_, select

from db.models import Audit, Order, ProductionEvent, Service
from services.estimativa_service import (
    ProductionEstimate, duration_label, estimate_production, workdays_label,
)
from services.producao_service import (
    ACTIVE_PRODUCTION_STATUSES, CLOSED_PRODUCTION_STATUSES,
    finish_production, production_clock, saved_estimate, start_production,
)
from ui.live_timer import render_timer
from utils.formatters import date_br, datetime_br, decimal_br, money_br


ACTIVE = ACTIVE_PRODUCTION_STATUSES
FINISHED = CLOSED_PRODUCTION_STATUSES
PAGE_SIZE = 50


def _reset_page() -> None:
    st.session_state["operation_page"] = 1


def _status_label(status: str) -> str:
    if status in ACTIVE:
        return "EM PRODUÇÃO"
    return status.replace("_", " ")


def _responsible(event: ProductionEvent | None) -> str:
    if event and event.observation and event.observation.startswith("Responsavel: "):
        return event.observation.removeprefix("Responsavel: ")
    return ""


def _event_time(event: ProductionEvent | None) -> str:
    if not event:
        return ""
    at = event.at if event.at.tzinfo else event.at.replace(tzinfo=timezone.utc)
    return datetime_br(at)


def _orders(session, term: str, group: str, page: int):
    conditions = []
    if term.strip():
        pattern = f"%{term.strip()}%"
        conditions.append(or_(
            Order.codigo_interno.ilike(pattern), Order.cliente_pdf.ilike(pattern),
            Order.carrinho.ilike(pattern), Order.cidade.ilike(pattern),
            Order.services.any(or_(Service.codigo_servico.ilike(pattern),
                                   Service.cliente_origem.ilike(pattern))),
        ))
    if group == "Em produção":
        conditions.append(Order.status_producao.in_(ACTIVE))
    elif group == "Concluídos":
        conditions.append(Order.status_producao.in_(FINISHED))
    elif group == "Aguardando":
        conditions.append(Order.status_producao.not_in(ACTIVE | FINISHED))
    total = session.scalar(select(func.count(Order.id)).where(*conditions)) or 0
    orders = session.scalars(select(Order).where(*conditions).order_by(
        Order.created_at.desc(), Order.codigo_interno,
    ).limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)).all()
    return orders, total


def _rows(session, orders, *, include_meta: bool = False):
    if not orders:
        return []
    ids = [order.id for order in orders]
    manual_delivery_ids = set(session.scalars(select(Audit.entity_id).where(
        Audit.entity == "pedidos", Audit.field == "previsao_entrega",
        Audit.source == "ROTA", Audit.entity_id.in_([str(order_id) for order_id in ids]),
    )).all())
    services = defaultdict(list)
    for item in session.scalars(select(Service).where(Service.pedido_id.in_(ids))):
        services[item.pedido_id].append(item)
    estimates = {}
    for entity_id, raw_value in session.execute(select(
        Audit.entity_id, Audit.new_value,
    ).where(
        Audit.entity == "pedidos", Audit.field == "estimativa_producao",
        Audit.source == "PRODUCAO", Audit.entity_id.in_([str(order_id) for order_id in ids]),
    ).order_by(Audit.at)):
        try:
            estimates[entity_id] = ProductionEstimate.from_snapshot(json.loads(raw_value))
        except (KeyError, TypeError, ValueError):
            continue
    events = defaultdict(dict)
    for event in session.scalars(select(ProductionEvent).where(
        ProductionEvent.pedido_id.in_(ids),
        ProductionEvent.type.in_(("INICIAR_CORTE", "FINALIZAR_PRODUCAO")),
    ).order_by(ProductionEvent.at)):
        events[event.pedido_id][event.type] = event

    result = []
    for order in orders:
        work = services[order.id]
        estimate = estimates.get(str(order.id)) or estimate_production(work)
        first = work[0] if work else None
        start = events[order.id].get("INICIAR_CORTE")
        finish = events[order.id].get("FINALIZAR_PRODUCAO")
        if order.status_producao in ACTIVE or (start and finish and finish.at < start.at):
            finish = None
        elapsed = ""
        if start and finish:
            minutes = max(0, int((finish.at - start.at).total_seconds() // 60))
            elapsed = f"{minutes // 60:02d}h {minutes % 60:02d}min"
        row = {
            "Pedido": order.codigo_interno,
            "Data pedido": date_br(order.data_pedido),
            "Cliente": order.cliente_pdf or (first.cliente_origem if first else None),
            "Vendedor": order.vendedor_pdf or (first.vendedor_origem if first else None),
            "Loja": order.loja_venda,
            "Carrinho": order.carrinho,
            "Cidade": order.cidade,
            "Endereco": order.endereco,
            "Bairro": order.bairro,
            "Peso": decimal_br(order.peso),
            "Total": money_br(order.valor_total),
            "Servicos": ", ".join(item.codigo_servico for item in work),
            "Linha": ", ".join(dict.fromkeys(
                item.linha_producao for item in work if item.linha_producao)),
            "Chapas": decimal_br(sum((item.chapas or 0 for item in work), Decimal(0))),
            "Cortes": decimal_br(sum((item.cortes or 0 for item in work), Decimal(0))),
            "Fita": decimal_br(sum((item.fita_aplicada or 0 for item in work), Decimal(0))),
            "Usinagens": decimal_br(sum((item.usinagens or 0 for item in work), Decimal(0))),
            "Carga": date_br(order.data_carregamento),
            "Entrega": date_br(order.previsao_entrega) if str(order.id) in manual_delivery_ids else "",
            "Programado": date_br(order.programado_em),
            "Turno": order.turno,
            "Inicio producao": _event_time(start),
            "Iniciado por": _responsible(start),
            "Fim producao": _event_time(finish),
            "Encerrado por": _responsible(finish),
            "Tempo producao": elapsed,
            "Tempo estimado": duration_label(estimate.total_seconds) if estimate.total_seconds else "Sem medidas",
            "Status": _status_label(order.status_producao),
            "Observacoes": order.observacao_operacional,
        }
        if include_meta:
            row["_start_at"] = start.at if start else None
            row["_estimate_seconds"] = estimate.total_seconds
        result.append(row)
    return result


def _render_content(factory, user):
    st.caption("Dados importados por pedido. Início e fim são informados pela operação.")
    feedback = st.session_state.pop("operation_production_feedback", None)
    if feedback:
        st.success(feedback)
    search_col, filter_col, page_col = st.columns([3, 2, 1])
    term = search_col.text_input("Buscar pedido, cliente, carrinho ou cidade",
                                 key="operation_search", on_change=_reset_page)
    group = filter_col.selectbox("Situação", ["Todos", "Aguardando", "Em produção", "Concluídos"],
                                 key="operation_status", on_change=_reset_page)
    page = int(page_col.number_input("Página", min_value=1, step=1, key="operation_page"))
    with factory() as session:
        orders, total = _orders(session, term, group, page)
        data = _rows(session, orders)
    st.caption(f"{total} pedido(s) encontrado(s) · página {page} de {max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)}")
    st.dataframe(data, hide_index=True, use_container_width=True, height=360,
                 key="operation_spreadsheet")
    if not orders:
        st.info("Nenhum pedido encontrado. Importe a planilha de serviços ou ajuste a busca.")
        return

    by_id = {str(order.id): order for order in orders}
    selected_id = st.selectbox("Pedido para registrar produção", list(by_id),
                               format_func=lambda key: (
                                   f"{by_id[key].codigo_interno} · "
                                   f"{by_id[key].cliente_pdf or by_id[key].cidade or 'sem cliente'} · "
                                   f"{_status_label(by_id[key].status_producao)}"
                               ), key="operation_selected_order")
    selected = by_id[selected_id]
    st.caption(f"Status atual: {_status_label(selected.status_producao)}")
    with factory() as session:
        current_order = session.get(Order, selected.id)
        selected_services = session.scalars(select(Service).where(Service.pedido_id == selected.id)).all()
        estimate = saved_estimate(session, selected.id) or estimate_production(selected_services)
        clock = production_clock(session, current_order) if current_order else None
    if clock:
        render_timer(clock, selected.codigo_interno)
    if estimate.total_seconds:
        st.markdown("#### Tempo estimado para este pedido")
        cut_col, drill_col, edge_col, total_col = st.columns(4)
        cut_col.metric("Corte", duration_label(estimate.cut_seconds),
                       help=f"{decimal_br(estimate.cuts)} cortes · 3.200 em 16h")
        drill_col.metric("Furação", duration_label(estimate.drilling_seconds),
                         help=f"{decimal_br(estimate.drillings)} usinagens · 5.000 em 16h")
        edge_col.metric("Fita", duration_label(estimate.edge_seconds),
                        help=f"{decimal_br(estimate.edge_meters)} m · 1.800 m em 16h")
        total_col.metric("Total estimado", duration_label(estimate.total_seconds))
        stage_mode = "em sequência" if estimate.mode == "SEQUENCIAL" else "em paralelo"
        st.caption(f"Equivale a {workdays_label(estimate.total_seconds)}. "
                   "Furação usa a quantidade de usinagens da planilha. "
                   f"O cálculo considera etapas {stage_mode} e não inclui filas ou paradas.")
    else:
        st.warning("Este pedido não tem quantidades de cortes, usinagens ou fita para estimar o tempo.")
    if user.role not in {"ADMIN", "GESTOR", "OPERADOR"}:
        return
    responsible = st.text_input("Quem está registrando?", placeholder="Seu nome",
                                max_chars=160, key="operation_responsible")
    if selected.status_producao in ACTIVE:
        confirm = st.checkbox("Confirmo que a produção deste pedido terminou",
                              key="operation_finish_confirm")
        if st.button("Encerrar produção agora", type="primary",
                     disabled=not responsible.strip() or not confirm,
                     key="operation_finish_button"):
            try:
                with factory.begin() as session:
                    order = session.scalar(select(Order).where(
                        Order.id == selected.id).with_for_update())
                    if order is None:
                        raise ValueError("Pedido nao encontrado.")
                    finish_production(session, order, user.id, user.role, responsible)
                st.session_state["operation_production_feedback"] = (
                    f"Produção do pedido {selected.codigo_interno} encerrada.")
                st.rerun()
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))
        return
    if selected.status_producao in FINISHED:
        st.info("Este pedido já teve a produção encerrada.")
        return
    if selected.falta_material:
        st.warning("Resolva a falta de material antes de iniciar a produção.")
        return

    mode = st.radio("Início da produção", ["Iniciou agora", "Escolher data e hora"],
                    horizontal=True, key="operation_start_mode")
    chosen_at = None
    if mode == "Escolher data e hora":
        local_now = datetime.now(ZoneInfo(os.getenv("APP_TIMEZONE", "America/Sao_Paulo")))
        date_col, time_col = st.columns(2)
        chosen_day = date_col.date_input("Data do início", value=local_now.date(), format="DD/MM/YYYY",
                                         key="operation_start_date")
        chosen_time = time_col.time_input("Hora do início", value=local_now.time().replace(second=0,
                                           microsecond=0), step=60, key="operation_start_time")
        chosen_at = datetime.combine(chosen_day, chosen_time, local_now.tzinfo)
    if st.button("Registrar início da produção", type="primary",
                 disabled=not responsible.strip(), key="operation_start_button"):
        try:
            with factory.begin() as session:
                order = session.scalar(select(Order).where(
                    Order.id == selected.id).with_for_update())
                if order is None:
                    raise ValueError("Pedido nao encontrado.")
                start_production(session, order, user.id, user.role, chosen_at, responsible)
            st.session_state["operation_production_feedback"] = (
                f"Produção do pedido {selected.codigo_interno} iniciada. "
                f"Tempo estimado: {duration_label(estimate.total_seconds) if estimate.total_seconds else 'sem medidas'}.")
            st.rerun()
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))


@st.fragment
def render(factory, user):
    with st.expander("Pedidos e produção", expanded=True):
        _render_content(factory, user)
