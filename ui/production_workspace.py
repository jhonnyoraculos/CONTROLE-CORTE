"""Production page with the per-order spreadsheet and start/finish controls."""

import uuid
from datetime import datetime, timedelta, timezone

import streamlit as st
from sqlalchemy import select

from db.models import Order
from services.producao_service import finish_production, production_clock
from ui.operation_grid import render as render_grid
from ui.styles import page_header


@st.fragment(run_every="10s")
def _deadline_prompt(factory, user):
    if user.role not in {"ADMIN", "GESTOR", "OPERADOR"}:
        return
    selected_id = st.session_state.get("operation_selected_order")
    if not selected_id:
        return
    try:
        order_id = uuid.UUID(selected_id)
    except (TypeError, ValueError):
        return
    with factory() as session:
        order = session.get(Order, order_id)
        clock = production_clock(session, order) if order else None
        code = order.codigo_interno if order else ""
    if not clock or not clock.is_due:
        return
    now = datetime.now(timezone.utc)
    snooze_key = f"deadline_snooze_{order_id}"
    snoozed_until = st.session_state.get(snooze_key, now)
    if snoozed_until > now:
        st.caption("O prazo estimado terminou. O lembrete foi adiado por 10 minutos.")
        return

    st.warning(f"O tempo estimado do pedido {code} terminou. A produção acabou?")
    responsible = st.text_input("Quem confirma o término?", key=f"deadline_responsible_{order_id}")
    yes_col, later_col = st.columns(2)
    if yes_col.button("Sim, encerrar produção", type="primary",
                      disabled=not responsible.strip(), key=f"deadline_yes_{order_id}"):
        try:
            with factory.begin() as session:
                current = session.scalar(select(Order).where(Order.id == order_id).with_for_update())
                if current is None:
                    raise ValueError("Pedido nao encontrado.")
                finish_production(session, current, user.id, user.role, responsible)
            st.session_state["operation_production_feedback"] = (
                f"Produção do pedido {code} encerrada após a confirmação.")
            st.rerun()
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))
    if later_col.button("Ainda não terminou", key=f"deadline_later_{order_id}"):
        st.session_state[snooze_key] = now + timedelta(minutes=10)


def render(factory, user):
    page_header(
        "Produção",
        "Consulte os pedidos e registre o início ou o fim da produção.",
        "Andamento por pedido",
        "Sistema ativo",
        "P",
    )
    render_grid(factory, user)
    _deadline_prompt(factory, user)
