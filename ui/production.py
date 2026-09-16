"""Filtered shop floor queue and guarded production actions."""

from datetime import date

import streamlit as st
from sqlalchemy import select

from config.constants import PROCESS_EVENTS
from db.models import Machine
from db.repositories.orders import order_detail, search_orders
from services.pedido_service import order_totals
from services.producao_service import record_event
from services.status_flow import get_statuses
from utils.formatters import date_br


def _flags(order) -> str:
    labels = []
    if order.previsao_entrega and order.previsao_entrega < date.today() and order.status_producao not in {
        "ENTREGUE", "RETIRADO", "CANCELADO"
    }:
        labels.append("🔴 Atrasado")
    if order.falta_material:
        labels.append("🟠 Falta material")
    if not order.carrinho:
        labels.append("🟡 Aguardando carrinho")
    if order.status_producao in {"EM_CORTE", "EM_FITAGEM", "EM_USINAGEM"}:
        labels.append("🔵 Em produção")
    if order.status_producao in {"PRODUCAO_FINALIZADA", "ENTREGUE", "RETIRADO"}:
        labels.append("🟢 Concluído")
    return " · ".join(labels)


def render(factory, user):
    st.title("Fila de produção")
    term = st.text_input("Busca rápida", key="queue_search")
    with factory() as session:
        a, b, c = st.columns(3)
        status = a.selectbox("Status", ["Todos"] + get_statuses(session))
        priority = b.selectbox("Prioridade", ["Todas", "BAIXA", "NORMAL", "ALTA", "URGENTE"])
        machines = session.scalars(select(Machine).where(Machine.active.is_(True)).order_by(Machine.name)).all()
        machine_options = {"Todas": None, **{machine.name: machine.id for machine in machines}}
        machine_name = c.selectbox("Máquina", list(machine_options))
        d, e = st.columns(2)
        seller = d.text_input("Vendedor contém")
        line = e.text_input("Linha de produção contém")
        sort_labels = {"Mais recentes": "newest", "Previsão mais próxima": "due",
                       "Maior prioridade": "priority"}
        sort_name = st.selectbox("Ordenar por", list(sort_labels))
        page = st.number_input("Página", min_value=1, value=1, step=1)
        rows, total = search_orders(session, term, None if status == "Todos" else status,
                                    limit=100, offset=(page - 1) * 100,
                                    seller=seller, line=line,
                                    machine_id=machine_options[machine_name],
                                    priority=None if priority == "Todas" else priority,
                                    sort=sort_labels[sort_name])
        summaries = []
        for item in rows:
            order = order_detail(session, item.id)
            totals = order_totals(order)
            summaries.append({"Prioridade": order.prioridade, "Pedido": order.codigo_interno,
                              "Cliente": order.cliente_pdf or (order.services[0].cliente_origem if order.services else "—"),
                              "Serviços": ", ".join(s.codigo_servico for s in order.services),
                              "Carrinho": order.carrinho, "Previsão": date_br(order.previsao_entrega),
                              "Programado": date_br(order.programado_em),
                              "Carga": date_br(order.data_carregamento), "Chapas": totals["chapas"],
                              "Cortes": totals["cortes"], "Fita": totals["fita_aplicada"],
                              "Usinagens": totals["usinagens"],
                              "Máquina": session.get(Machine, order.maquina_id).name if order.maquina_id else None,
                              "Status": order.status_producao,
                              "Sinalização": _flags(order)})
        st.caption(f"{total} pedidos · página {page} de {max(1, (total + 99) // 100)}")
        st.dataframe(summaries, hide_index=True, use_container_width=True)
        if not rows:
            return
        code = st.selectbox("Pedido para operação", [o.codigo_interno for o in rows])
        selected = next(o for o in rows if o.codigo_interno == code)
        if st.button("Abrir ficha do pedido"):
            st.query_params["pedido"] = code
            page = st.session_state.get("orders_page")
            if page:
                st.switch_page(page)
        if user.role not in {"ADMIN", "GESTOR", "OPERADOR"}:
            return
        event = st.selectbox("Ação", list(PROCESS_EVENTS))
        observation = st.text_input("Observação opcional")
        confirm = st.checkbox(f"Confirmo a ação {event.replace('_', ' ').lower()} no pedido {code}")
        if st.button("Registrar evento", type="primary", disabled=not confirm):
            try:
                with factory.begin() as tx:
                    order = tx.get(type(selected), selected.id)
                    record_event(tx, order, event, user.id, user.role, observation=observation)
                st.success("Evento registrado.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
