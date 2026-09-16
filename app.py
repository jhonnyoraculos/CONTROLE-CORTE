"""Streamlit entry point. Domain rules live in services and importers."""

import logging
import uuid
from pathlib import Path
from types import SimpleNamespace

import streamlit as st
from sqlalchemy import select

from config.settings import get_initial_admin, get_settings
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
        st.error(f"Nao foi possivel carregar a pagina. Referencia: {reference}")


def _login_user(factory):
    user_id = st.session_state.get("user_id")
    with factory() as session:
        user = session.get(User, uuid.UUID(user_id)) if user_id else None
        if user and not user.active:
            user = None
    return user


def _render_login(factory) -> None:
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
            st.error(f"Nao foi possivel validar o acesso. Referencia: {reference}")
            return
        st.error("E-mail ou senha invalidos.")


def _bootstrap_admin(factory) -> bool:
    initial_admin = get_initial_admin()
    if initial_admin:
        from services.auth_service import create_user

        try:
            with factory.begin() as session:
                if session.scalar(select(User.id).limit(1)) is None:
                    create_user(session, *initial_admin, "ADMIN")
        except Exception:
            logging.exception("ADMIN_BOOTSTRAP_FAILED")
            st.error("Nao foi possivel criar o administrador inicial. Confira os Secrets de implantacao.")
            return False
        st.rerun()
    st.title("Configuracao inicial")
    st.info(
        "Defina INITIAL_ADMIN_EMAIL e INITIAL_ADMIN_PASSWORD nos Secrets do Streamlit "
        "ou crie o administrador pelo comando abaixo; depois atualize a pagina."
    )
    st.code('python -m db.seed --name "Administrador" --email "admin@empresa.com"')
    return False


def _current_user(factory, has_user: bool, auth_enabled: bool):
    if not auth_enabled:
        st.sidebar.caption("Acesso sem login")
        return SimpleNamespace(id=None, name="Operador", role="ADMIN", active=True)
    if not has_user:
        _bootstrap_admin(factory)
        return None
    user = _login_user(factory)
    if not user:
        _render_login(factory)
        return None
    st.sidebar.caption(f"{user.name} - {user.role}")
    if st.sidebar.button("Sair"):
        log_event("LOGOUT", user_id=user.id)
        st.session_state.pop("user_id", None)
        st.rerun()
    return user


def main():
    logo_path = Path(__file__).with_name("logo-jr.png")
    page_icon = str(logo_path) if logo_path.exists() else None
    st.set_page_config(page_title="Controle do Corte", page_icon=page_icon, layout="wide")
    configure_logging()
    inject_styles()
    if logo_path.exists():
        st.logo(str(logo_path), size="large")
        st.sidebar.image(str(logo_path), width=82)
    try:
        settings = get_settings()
        configured_url = settings.database_url
    except Exception:
        logging.exception("SECRETS_INVALID")
        st.error("Nao foi possivel ler os Secrets do Streamlit. Confira o formato TOML.")
        return
    if not configured_url:
        st.error("DATABASE_URL nao encontrada nos Secrets do Streamlit.")
        st.code('DATABASE_URL = "postgresql://USUARIO:SENHA@HOST/BANCO?sslmode=require"')
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
            st.error("Banco conectado, mas as tabelas ainda nao foram criadas.")
            st.code("alembic upgrade head")
        else:
            st.error(f"Nao foi possivel conectar ao banco. Referencia: {reference}")
            st.caption("Confira se o Secret DATABASE_URL esta igual ao painel do Neon.")
        return

    user = _current_user(factory, has_user, settings.auth_enabled)
    if not user:
        return

    from ui import admin, imports, orders

    orders_page = st.Page(
        lambda: _render_page(orders.render, factory, user),
        title="Pedidos",
        url_path="pedidos",
    )
    st.session_state["orders_page"] = orders_page
    pages = [
        st.Page(
            lambda: _render_page(imports.render, factory, user),
            title="Operacao",
            url_path="operacao",
            default=True,
        ),
        orders_page,
    ]
    if user.role == "ADMIN":
        pages.append(st.Page(lambda: _render_page(admin.render, factory, user), title="Administracao", url_path="administracao"))
    st.navigation(pages).run()


if __name__ == "__main__":
    main()
