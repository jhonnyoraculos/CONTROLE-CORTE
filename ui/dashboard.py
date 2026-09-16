"""Compact operational overview backed by aggregate SQL queries."""

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import and_, case, func, select

from db.models import Machine, Order, ProductionEvent, Service
from services.status_flow import get_statuses
from ui.reports import (
    CLOSED,
    DATE_COLUMNS,
    FINISHED,
    ReportFilters,
    _order_conditions,
    average_times,
    capacity_page,
    production_trend,
    volume_page,
)
from ui.styles import page_header
from utils.formatters import date_br, decimal_br


def _pick(row: dict, *keys):
    for key in keys:
        if key in row:
            return row[key]
    return None


def _date_key(label_part: str) -> str:
    return next(key for key in DATE_COLUMNS if label_part in key)


def _metrics(session, today: date, filters: ReportFilters | None = None) -> dict[str, int | Decimal]:
    conditions = _order_conditions(filters) if filters else []
    open_condition = and_(Order.status_producao.not_in(FINISHED), Order.status_producao != "CANCELADO")
    overdue_condition = and_(Order.previsao_entrega < today, Order.status_producao.not_in(CLOSED))
    row = session.execute(select(
        func.count(Order.id),
        func.sum(case((open_condition, 1), else_=0)),
        func.sum(case((Order.status_producao.in_(FINISHED), 1), else_=0)),
        func.sum(case((overdue_condition, 1), else_=0)),
        func.sum(case((Order.falta_material.is_(True), 1), else_=0)),
        func.sum(case((Order.status_dados == "AGUARDANDO_CARRINHO", 1), else_=0)),
        func.sum(case((Order.programado_em == today, 1), else_=0)),
        func.sum(case((Order.status_producao.in_(("EM_CORTE", "EM_FITAGEM", "EM_USINAGEM")), 1), else_=0)),
    ).where(*conditions)).one()
    result = dict(zip(
        (
            "total",
            "abertos",
            "finalizados",
            "atrasados",
            "falta_material",
            "aguardando_carrinho",
            "programados_hoje",
            "em_producao",
        ),
        (int(value or 0) for value in row),
    ))
    planned_ids = select(Order.id).where(Order.programado_em == today, *conditions)
    planned = session.execute(select(func.sum(Service.chapas), func.sum(Service.cortes)).where(
        Service.pedido_id.in_(planned_ids)
    )).one()
    result["chapas_hoje"] = planned[0] or Decimal(0)
    result["cortes_hoje"] = planned[1] or Decimal(0)
    zone = ZoneInfo(os.getenv("APP_TIMEZONE", "America/Sao_Paulo"))
    day_start = datetime.combine(today, time.min, tzinfo=zone).astimezone(timezone.utc)
    day_end = datetime.combine(today + timedelta(days=1), time.min, tzinfo=zone).astimezone(timezone.utc)
    result["finalizados_hoje"] = session.scalar(select(func.count(func.distinct(
        ProductionEvent.pedido_id
    ))).join(Order, Order.id == ProductionEvent.pedido_id).where(
        ProductionEvent.type == "FINALIZAR_PRODUCAO",
        ProductionEvent.at >= day_start,
        ProductionEvent.at < day_end,
        *conditions,
    )) or 0
    return result


def _attention_rows(session, today: date, kind: str,
                    filters: ReportFilters | None = None) -> list[dict]:
    condition = (
        and_(Order.previsao_entrega < today, Order.status_producao.not_in(CLOSED))
        if kind == "atrasados"
        else Order.falta_material.is_(True)
    )
    rows = session.execute(select(
        Order.codigo_interno,
        Order.cliente_pdf,
        Order.previsao_entrega,
        Order.status_producao,
    ).where(
        condition,
        *(_order_conditions(filters) if filters else []),
    ).order_by(Order.previsao_entrega, Order.codigo_interno).limit(10)).all()
    return [
        {"Pedido": code, "Cliente": client or "", "Previsao": date_br(forecast), "Status": status}
        for code, client, forecast, status in rows
    ]


def render(factory, user) -> None:
    page_header(
        "Painel de producao",
        "Acompanhe pedidos, atrasos, capacidade e conclusoes do corte.",
        "Controle do corte",
        "Sistema ativo",
        "P",
    )
    today = date.today()
    with factory() as session:
        machines = session.execute(select(Machine.id, Machine.name).where(
            Machine.active.is_(True)
        ).order_by(Machine.name)).all()
        machine_options = {"Todas": None, **{name: machine_id for machine_id, name in machines}}

        with st.form("dashboard_filters"):
            st.markdown("#### Filtros")
            a, b, c = st.columns(3)
            start = a.date_input("De", today - timedelta(days=30), format="DD/MM/YYYY", key="dash_start")
            end = b.date_input("Ate", today + timedelta(days=30), format="DD/MM/YYYY", key="dash_end")
            basis_options = {
                "Previsao de entrega": _date_key("entrega"),
                "Programacao": _date_key("Programa"),
                "Data do pedido": "Data do pedido",
            }
            basis_label = c.selectbox(
                "Data dos pedidos",
                tuple(basis_options),
                key="dash_basis",
            )
            basis = basis_options[basis_label]
            d, e, f = st.columns(3)
            status = d.selectbox("Status", ("Todos", *get_statuses(session)), key="dash_status")
            machine = e.selectbox("Maquina", list(machine_options), key="dash_machine")
            city = f.text_input("Cidade contem", key="dash_city")
            g, h, i = st.columns(3)
            seller = g.text_input("Vendedor contem", key="dash_seller")
            customer = h.text_input("Cliente contem", key="dash_customer")
            line = i.text_input("Linha contem", key="dash_line")
            j, k, l = st.columns(3)
            store = j.text_input("Loja contem", key="dash_store")
            modality = k.text_input("Modalidade contem", key="dash_modality")
            central = l.text_input("Central contem", key="dash_central")
            st.form_submit_button("Aplicar filtros", type="primary")

        if start > end:
            st.error("A data inicial deve ser anterior ou igual a data final.")
            return
        filters = ReportFilters(
            start,
            end,
            basis,
            None if status == "Todos" else status,
            machine_options[machine],
            city,
            seller,
            customer,
            line,
            store,
            modality,
            central,
        )
        st.caption(f"Indicadores filtrados por {basis_label.lower()}: {start:%d/%m/%Y} a {end:%d/%m/%Y}.")
        metrics = _metrics(session, today, filters)
        captions = (
            ("Pedidos", "total"),
            ("Abertos", "abertos"),
            ("Finalizados", "finalizados"),
            ("Atrasados", "atrasados"),
            ("Falta de material", "falta_material"),
            ("Aguardando carrinho", "aguardando_carrinho"),
            ("Programados hoje", "programados_hoje"),
            ("Em producao", "em_producao"),
            ("Finalizados hoje", "finalizados_hoje"),
            ("Chapas hoje", "chapas_hoje"),
            ("Cortes hoje", "cortes_hoje"),
        )
        for offset in range(0, len(captions), 4):
            for column, (label, key) in zip(st.columns(4), captions[offset:offset + 4]):
                value = decimal_br(metrics[key]) if key in {"chapas_hoje", "cortes_hoje"} else metrics[key]
                column.metric(label, value)

        st.markdown("#### Producao concluida")
        b, c = st.columns([1, 1])
        frequency = b.selectbox("Agrupamento", ("Diaria", "Semanal", "Mensal"))
        dimension_options = {"Maquina": "MÃ¡quina", "Vendedor": "Vendedor", "Cidade": "Cidade", "Cliente": "Cliente"}
        dimension_label = c.selectbox("Volume por", tuple(dimension_options))
        dimension = dimension_options[dimension_label]
        trend = production_trend(session, filters, frequency)
        left, right = st.columns([3, 2])
        with left:
            if trend:
                figure = go.Figure(go.Bar(
                    x=[_pick(item, "Período", "PerÃ­odo", "Periodo") for item in trend],
                    y=[item["Pedidos finalizados"] for item in trend],
                    marker_color="#327d90",
                ))
                figure.update_layout(height=320, margin=dict(l=20, r=20, t=20, b=60))
                st.plotly_chart(figure, use_container_width=True)
            else:
                st.info("Ainda nao ha conclusao registrada neste periodo.")
        with right:
            averages = average_times(session, filters)
            st.markdown("**Tempo medio ate a conclusao**")
            for row in averages:
                hours = _pick(row, "Tempo médio (h)", "Tempo mÃ©dio (h)", "Tempo medio (h)")
                st.metric(row["Etapa"], f"{hours:.1f} h" if hours is not None else "-", help=f"{row['Pedidos medidos']} pedidos medidos")

        left, right = st.columns(2)
        with left:
            st.markdown(f"#### Volume por {dimension_label.lower()}")
            _, volume = volume_page(session, filters, dimension, 1, 10)
            if volume:
                st.dataframe(volume, hide_index=True, use_container_width=True)
            else:
                st.info("Sem pedidos para os filtros selecionados.")
        with right:
            st.markdown("#### Capacidade no periodo")
            _, capacity = capacity_page(session, filters, 1, 30)
            if capacity:
                st.dataframe(capacity, hide_index=True, use_container_width=True)
                if any(row["Excedida"] == "Sim" for row in capacity):
                    st.warning("Ha capacidade excedida em pelo menos um processo.")
            else:
                st.info("Configure capacidades por data para acompanhar a carga.")

        left, right = st.columns(2)
        with left:
            st.markdown("#### Proximos atrasados")
            overdue = _attention_rows(session, today, "atrasados", filters)
            st.dataframe(overdue, hide_index=True, use_container_width=True)
        with right:
            st.markdown("#### Pedidos com falta de material")
            shortages = _attention_rows(session, today, "material", filters)
            st.dataframe(shortages, hide_index=True, use_container_width=True)
