"""Production schedule with impact preview before saving."""

from datetime import date

import streamlit as st
from sqlalchemy import select

from db.models import Machine, Order
from db.repositories.orders import order_detail, search_orders
from services.producao_service import plan_order, preview_plan
from services.status_flow import get_shifts


def render(factory, user):
    st.title("Planejamento")
    day = st.date_input("Data de produção", value=date.today(), format="DD/MM/YYYY")
    with factory() as session:
        scheduled = session.scalars(select(Order).where(Order.programado_em == day).order_by(
            Order.prioridade.desc(), Order.codigo_interno).limit(100)).all()
        st.dataframe([{"Pedido": o.codigo_interno, "Cliente": o.cliente_pdf,
                       "Turno": o.turno, "Prioridade": o.prioridade,
                       "Status": o.status_producao} for o in scheduled],
                     hide_index=True, use_container_width=True)
        if user.role not in {"ADMIN", "GESTOR", "PLANEJAMENTO"}:
            return
        term = st.text_input("Pesquisar pedido a programar")
        orders, _ = search_orders(session, term, limit=50)
        if not orders:
            return
        code = st.selectbox("Pedido", [o.codigo_interno for o in orders])
        order = order_detail(session, next(o.id for o in orders if o.codigo_interno == code))
        machines = session.scalars(select(Machine).where(Machine.active.is_(True)).order_by(Machine.name)).all()
        labels = {"Sem máquina": None, **{m.name: m.id for m in machines}}
        machine_name = st.selectbox("Máquina", list(labels))
        shifts = get_shifts(session)
        if order.turno and order.turno not in shifts:
            shifts.append(order.turno)
        shift = st.selectbox("Turno", shifts,
                             index=shifts.index(order.turno) if order.turno else 0)
        priority = st.selectbox("Prioridade", ["BAIXA", "NORMAL", "ALTA", "URGENTE"],
                                index=["BAIXA", "NORMAL", "ALTA", "URGENTE"].index(order.prioridade))
        preview = preview_plan(session, order, day, labels[machine_name], shift)
        if preview:
            st.markdown("#### Impacto na capacidade")
            st.dataframe([{"Processo": row["process"], "Métrica": row["metric"],
                           "Uso atual": row["used"], "Após programação": row["projected"],
                           "Limite": row["limit"], "Situação": "ACIMA DA CAPACIDADE" if row["over"] else "OK"}
                          for row in preview], hide_index=True, use_container_width=True)
        else:
            st.info("Ainda não existe capacidade configurada para esta data. Configure os limites na Administração.")
        over = any(row["over"] for row in preview)
        if over:
            st.warning("A programação ultrapassará a capacidade configurada.")
        override = st.checkbox("Autorizar excesso de capacidade", disabled=user.role not in {"ADMIN", "GESTOR"})
        if st.button("Salvar programação", type="primary", disabled=over and not override):
            try:
                with factory.begin() as tx:
                    saved = tx.get(Order, order.id)
                    plan_order(tx, saved, day, shift, labels[machine_name], priority,
                               user.id, user.role, allow_over_capacity=override)
                st.success("Programação salva.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
