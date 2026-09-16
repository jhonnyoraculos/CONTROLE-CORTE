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
from ui.reports import (CLOSED, FINISHED, ReportFilters, average_times,
                        _order_conditions, capacity_page, production_trend, volume_page)
from utils.formatters import date_br, decimal_br


def _metrics(session, today: date, filters: ReportFilters | None = None) -> dict[str, int | Decimal]:
    conditions = _order_conditions(filters) if filters else []
    open_condition = and_(Order.status_producao.not_in(FINISHED),
                          Order.status_producao != "CANCELADO")
    overdue_condition = and_(Order.previsao_entrega < today,
                             Order.status_producao.not_in(CLOSED))
    row = session.execute(select(
        func.count(Order.id),
        func.sum(case((open_condition, 1), else_=0)),
        func.sum(case((Order.status_producao.in_(FINISHED), 1), else_=0)),
        func.sum(case((overdue_condition, 1), else_=0)),
        func.sum(case((Order.falta_material.is_(True), 1), else_=0)),
        func.sum(case((Order.status_dados == "AGUARDANDO_CARRINHO", 1), else_=0)),
        func.sum(case((Order.programado_em == today, 1), else_=0)),
        func.sum(case((Order.status_producao.in_(
            ("EM_CORTE", "EM_FITAGEM", "EM_USINAGEM")), 1), else_=0)),
    ).where(*conditions)).one()
    result = dict(zip(("total", "abertos", "finalizados", "atrasados",
                       "falta_material", "aguardando_carrinho", "programados_hoje",
                       "em_producao"), (int(value or 0) for value in row)))
    planned_ids = select(Order.id).where(Order.programado_em == today, *conditions)
    planned = session.execute(select(func.sum(Service.chapas), func.sum(Service.cortes)).where(
        Service.pedido_id.in_(planned_ids))).one()
    result["chapas_hoje"] = planned[0] or Decimal(0)
    result["cortes_hoje"] = planned[1] or Decimal(0)
    zone = ZoneInfo(os.getenv("APP_TIMEZONE", "America/Sao_Paulo"))
    day_start = datetime.combine(today, time.min, tzinfo=zone).astimezone(timezone.utc)
    day_end = datetime.combine(today + timedelta(days=1), time.min,
                               tzinfo=zone).astimezone(timezone.utc)
    result["finalizados_hoje"] = session.scalar(select(func.count(func.distinct(
        ProductionEvent.pedido_id))).join(Order, Order.id == ProductionEvent.pedido_id).where(
            ProductionEvent.type == "FINALIZAR_PRODUCAO",
            ProductionEvent.at >= day_start, ProductionEvent.at < day_end,
            *conditions)) or 0
    return result


def _attention_rows(session, today: date, kind: str,
                    filters: ReportFilters | None = None) -> list[dict]:
    condition = (and_(Order.previsao_entrega < today,
                      Order.status_producao.not_in(CLOSED))
                 if kind == "atrasados" else Order.falta_material.is_(True))
    rows = session.execute(select(Order.codigo_interno, Order.cliente_pdf,
                                  Order.previsao_entrega, Order.status_producao)
                           .where(condition, *(_order_conditions(filters) if filters else []))
                           .order_by(Order.previsao_entrega, Order.codigo_interno)
                           .limit(10)).all()
    return [{"Pedido": code, "Cliente": client or "", "Previsão": date_br(forecast),
             "Status": status} for code, client, forecast, status in rows]


def render(factory, user) -> None:
    st.title("Painel de produção")
    today = date.today()
    with factory() as session:
        machines = session.execute(select(Machine.id, Machine.name).where(
            Machine.active.is_(True)).order_by(Machine.name)).all()
        machine_options = {"Todas": None, **{name: machine_id for machine_id, name in machines}}
        with st.expander("Filtros do painel", expanded=False):
            a, b, c = st.columns(3)
            start = a.date_input("De", today - timedelta(days=30),
                                 format="DD/MM/YYYY", key="dash_start")
            end = b.date_input("Até", today + timedelta(days=30),
                               format="DD/MM/YYYY", key="dash_end")
            basis = c.selectbox("Data dos pedidos", ("Previsão de entrega", "Programação", "Data do pedido"),
                                key="dash_basis")
            d, e, f = st.columns(3)
            status = d.selectbox("Status", ("Todos", *get_statuses(session)), key="dash_status")
            machine = e.selectbox("Máquina", list(machine_options), key="dash_machine")
            city = f.text_input("Cidade contém", key="dash_city")
            g, h, i = st.columns(3)
            seller = g.text_input("Vendedor contém", key="dash_seller")
            customer = h.text_input("Cliente contém", key="dash_customer")
            line = i.text_input("Linha contém", key="dash_line")
            j, k, l = st.columns(3)
            store = j.text_input("Loja contém", key="dash_store")
            modality = k.text_input("Modalidade contém", key="dash_modality")
            central = l.text_input("Central contém", key="dash_central")
        if start > end:
            st.error("A data inicial deve ser anterior ou igual à data final.")
            return
        filters = ReportFilters(start, end, basis, None if status == "Todos" else status,
                                machine_options[machine], city, seller, customer, line,
                                store, modality, central)
        st.caption(f"Indicadores e listas filtrados por {basis.lower()}: {start:%d/%m/%Y} a {end:%d/%m/%Y}.")
        metrics = _metrics(session, today, filters)
        captions = (("Pedidos", "total"), ("Abertos", "abertos"),
                    ("Finalizados", "finalizados"), ("Atrasados", "atrasados"),
                    ("Falta de material", "falta_material"),
                    ("Aguardando carrinho", "aguardando_carrinho"),
                    ("Programados hoje", "programados_hoje"),
                    ("Em produção", "em_producao"),
                    ("Finalizados hoje", "finalizados_hoje"),
                    ("Chapas programadas hoje", "chapas_hoje"),
                    ("Cortes programados hoje", "cortes_hoje"))
        for offset in range(0, len(captions), 4):
            for column, (label, key) in zip(st.columns(4), captions[offset:offset + 4]):
                value = decimal_br(metrics[key]) if key in {"chapas_hoje", "cortes_hoje"} else metrics[key]
                column.metric(label, value)

        st.markdown("#### Produção concluída")
        b, c = st.columns([1, 1])
        frequency = b.selectbox("Agrupamento", ("Diária", "Semanal", "Mensal"))
        dimension = c.selectbox("Volume por", ("Máquina", "Vendedor", "Cidade", "Cliente"))
        trend = production_trend(session, filters, frequency)
        left, right = st.columns([3, 2])
        with left:
            if trend:
                figure = go.Figure(go.Bar(x=[item["Período"] for item in trend],
                                          y=[item["Pedidos finalizados"] for item in trend],
                                          marker_color="#327d90"))
                figure.update_layout(height=320, margin=dict(l=20, r=20, t=20, b=60))
                st.plotly_chart(figure, use_container_width=True)
            else:
                st.info("Ainda não há conclusão registrada neste período.")
        with right:
            averages = average_times(session, filters)
            st.markdown("**Tempo médio até a conclusão**")
            for row in averages:
                hours = row["Tempo médio (h)"]
                st.metric(row["Etapa"], f"{hours:.1f} h" if hours is not None else "—",
                          help=f"{row['Pedidos medidos']} pedidos medidos")

        left, right = st.columns(2)
        with left:
            st.markdown(f"#### Volume por {dimension.lower()}")
            _, volume = volume_page(session, filters, dimension, 1, 10)
            if volume:
                st.dataframe(volume, hide_index=True, use_container_width=True)
            else:
                st.info("Sem pedidos para os filtros selecionados.")
        with right:
            st.markdown("#### Capacidade no período")
            _, capacity = capacity_page(session, filters, 1, 30)
            if capacity:
                st.dataframe(capacity, hide_index=True, use_container_width=True)
                st.caption("Mostrando até 30 configurações de capacidade no período.")
                if any(row["Excedida"] == "Sim" for row in capacity):
                    st.warning("Há capacidade excedida em pelo menos um processo.")
            else:
                st.info("Configure capacidades por data para acompanhar a carga.")

        left, right = st.columns(2)
        with left:
            st.markdown("#### Próximos atrasados")
            overdue = _attention_rows(session, today, "atrasados", filters)
            st.dataframe(overdue, hide_index=True, use_container_width=True)
            if metrics["atrasados"] > len(overdue):
                st.caption("Mostrando os 10 primeiros. Consulte Relatórios para filtrar e exportar.")
        with right:
            st.markdown("#### Pedidos com falta de material")
            shortages = _attention_rows(session, today, "material", filters)
            st.dataframe(shortages, hide_index=True, use_container_width=True)
            if metrics["falta_material"] > len(shortages):
                st.caption("Mostrando os 10 primeiros. Consulte Relatórios para filtrar e exportar.")
