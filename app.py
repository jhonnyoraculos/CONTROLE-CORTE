"""Streamlit entry point. Domain rules live in services and importers."""

import logging
import uuid

import streamlit as st
from sqlalchemy import select

from db.engine import session_factory
from db.models import User
from ui.styles import inject_styles
from utils.logging import configure_logging, log_event


def _render_page(renderer, factory, user) -> None:
    try:
        renderer(factory, user)
    except Exception:
        reference = uuid.uuid4().hex[:8]
        logging.exception("PAGE_FAILED reference=%s", reference)
        st.error(f"Não foi possível carregar a página. Referência: {reference}")


def main():
    st.set_page_config(page_title="Controle do Corte", page_icon="🪚", layout="wide")
    configure_logging()
    inject_styles()
    try:
        factory = session_factory()
        with factory() as session:
            has_user = session.scalar(select(User.id).limit(1)) is not None
    except Exception:
        st.error("Banco de dados indisponível ou não configurado.")
        st.code("Configure DATABASE_URL e execute: alembic upgrade head")
        logging.exception("DATABASE_CONNECTION_FAILED")
        return
    if not has_user:
        st.title("Configuração inicial")
        st.info("Crie o primeiro administrador pelo comando abaixo e atualize a página.")
        st.code('python -m db.seed --name "Administrador" --email "admin@empresa.com"')
        return
    user_id = st.session_state.get("user_id")
    with factory() as session:
        user = session.get(User, uuid.UUID(user_id)) if user_id else None
        if user and not user.active:
            user = None
    if not user:
        from services.auth_service import authenticate

        st.title("Controle do Corte")
        with st.form("login"):
            email = st.text_input("E-mail")
            password = st.text_input("Senha", type="password")
            submitted = st.form_submit_button("Entrar", use_container_width=True)
        if submitted:
            try:
                with factory() as session:
                    found = authenticate(session, email, password)
                    if found:
                        st.session_state.user_id = str(found.id)
                        log_event("LOGIN", user_id=found.id)
                        st.rerun()
            except Exception:
                reference = uuid.uuid4().hex[:8]
                logging.exception("LOGIN_FAILED reference=%s", reference)
                st.error(f"Não foi possível validar o acesso. Referência: {reference}")
                return
            st.error("E-mail ou senha inválidos.")
        return
    st.sidebar.caption(f"{user.name} · {user.role}")
    if st.sidebar.button("Sair"):
        log_event("LOGOUT", user_id=user.id)
        st.session_state.pop("user_id", None)
        st.rerun()

    from ui import admin, dashboard, imports, orders, pending, planning, production, reports

    orders_page = st.Page(lambda: _render_page(orders.render, factory, user),
                          title="Pedidos", icon="📋", url_path="pedidos")
    st.session_state["orders_page"] = orders_page
    pages = [st.Page(lambda: _render_page(dashboard.render, factory, user),
                     title="Painel", icon="📊", url_path="painel", default=True),
             orders_page,
             st.Page(lambda: _render_page(production.render, factory, user),
                     title="Fila de produção", icon="🪚", url_path="fila"),
             st.Page(lambda: _render_page(planning.render, factory, user),
                     title="Planejamento", icon="🗓️", url_path="planejamento"),
             st.Page(lambda: _render_page(pending.render, factory, user),
                     title="Pendências", icon="⚠️", url_path="pendencias"),
             st.Page(lambda: _render_page(reports.render, factory, user),
                     title="Relatórios", icon="📈", url_path="relatorios")]
    if user.role in {"ADMIN", "GESTOR"}:
        pages.append(st.Page(lambda: _render_page(imports.render, factory, user),
                             title="Importações", icon="📥", url_path="importacoes"))
    if user.role == "ADMIN":
        pages.append(st.Page(lambda: _render_page(admin.render, factory, user),
                             title="Administração", icon="⚙️", url_path="administracao"))
    st.navigation(pages).run()


if __name__ == "__main__":
    main()
