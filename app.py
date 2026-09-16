"""Streamlit entry point. Domain rules live in services and importers."""

import logging
import uuid

import streamlit as st
from sqlalchemy import select

from db.engine import session_factory
from db.models import User
from config.settings import get_initial_admin, get_settings
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
        configured_url = get_settings().database_url
    except Exception:
        logging.exception("SECRETS_INVALID")
        st.error("Não foi possível ler os Secrets do Streamlit. Confira o formato TOML.")
        return
    if not configured_url:
        st.error("DATABASE_URL não encontrada nos Secrets do Streamlit.")
        st.code('DATABASE_URL = "postgresql+psycopg://USUARIO:SENHA@HOST/BANCO?sslmode=require"')
        return
    try:
        factory = session_factory()
        with factory() as session:
            has_user = session.scalar(select(User.id).limit(1)) is not None
    except Exception as exc:
        reference = uuid.uuid4().hex[:8]
        logging.exception("DATABASE_CONNECTION_FAILED reference=%s", reference)
        sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
        if sqlstate == "42P01":
            st.error("Banco conectado, mas as tabelas ainda não foram criadas.")
            st.code("alembic upgrade head")
        else:
            st.error(f"Não foi possível conectar ao banco. Referência: {reference}")
            st.caption("Confira o Secret DATABASE_URL e o prefixo postgresql+psycopg://.")
        return
    if not has_user:
        initial_admin = get_initial_admin()
        if initial_admin:
            from services.auth_service import create_user

            try:
                with factory.begin() as session:
                    if session.scalar(select(User.id).limit(1)) is None:
                        create_user(session, *initial_admin, "ADMIN")
            except Exception:
                logging.exception("ADMIN_BOOTSTRAP_FAILED")
                st.error("Não foi possível criar o administrador inicial. Confira os Secrets de implantação.")
                return
            st.rerun()
        st.title("Configuração inicial")
        st.info("Defina INITIAL_ADMIN_EMAIL e INITIAL_ADMIN_PASSWORD nos Secrets do Streamlit "
                "ou crie o administrador pelo comando abaixo; depois atualize a página.")
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
