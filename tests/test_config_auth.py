"""Configuration and authorization are enforced by services, not UI visibility."""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
import pytest

from config.constants import PRODUCTION_STATUSES
from config.settings import normalize_database_url
from db.models import Audit, Base, Order, User
from services.admin_service import create_machine, save_setting, update_machine, update_user
from services.auth_service import create_user, passwords
from services.note_service import add_note
from services.producao_service import record_event
from services.status_flow import get_flow, get_shifts, save_flow, save_shifts, save_statuses


def test_neon_database_url_is_accepted_as_copied() -> None:
    raw = "postgresql://user:pass@example.neon.tech/db?sslmode=require"
    assert normalize_database_url(raw) == (
        "postgresql+psycopg://user:pass@example.neon.tech/db?sslmode=require"
    )
    explicit = "postgresql+psycopg://user:pass@example.neon.tech/db?sslmode=require"
    assert normalize_database_url(explicit) == explicit
    sqlite = "sqlite:///local.sqlite"
    assert normalize_database_url(sqlite) == sqlite


def test_admin_configured_flow_and_service_permissions(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'config.sqlite').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        admin = create_user(session, "Admin", "admin@example.com", "OriginalPass123!", "ADMIN")
        viewer = create_user(session, "Viewer", "viewer@example.com", "ViewerPass123!",
                             "CONSULTA", actor_role="ADMIN", actor_id=admin.id)
        order = Order(codigo_interno="A1", codigo_interno_normalizado="A1",
                      status_producao="CUSTOM_READY")
        session.add(order)
        session.flush()
        save_shifts(session, ["MANHÃ", "TARDE"], admin.id, "ADMIN")
        assert get_shifts(session) == ["MANHÃ", "TARDE"]
        save_statuses(session, [*PRODUCTION_STATUSES, "CUSTOM_READY"], admin.id, "ADMIN")
        flow = get_flow(session)
        flow["INICIAR_CORTE"] = {"from": ["CUSTOM_READY"], "to": "EM_CORTE"}
        save_flow(session, flow, admin.id, "ADMIN")
        with pytest.raises(PermissionError):
            save_shifts(session, ["NOITE"], viewer.id, "CONSULTA")
        with pytest.raises(PermissionError):
            record_event(session, order, "INICIAR_CORTE", viewer.id, "CONSULTA")
        record_event(session, order, "INICIAR_CORTE", admin.id, "ADMIN")
        assert order.status_producao == "EM_CORTE"
        with pytest.raises(PermissionError):
            add_note(session, order, viewer.id, "CONSULTA", "Teste")
        add_note(session, order, admin.id, "ADMIN", "Material conferido")
        with pytest.raises(PermissionError):
            save_setting(session, "max_upload_mb", 30, viewer.id, "CONSULTA")
        save_setting(session, "max_upload_mb", 30, admin.id, "ADMIN")
        with pytest.raises(PermissionError):
            update_user(session, viewer, viewer.id, "CONSULTA", "ADMIN", True)
        update_user(session, viewer, admin.id, "ADMIN", "OPERADOR", True, "NewSecurePass123!")
        assert not passwords.verify("ViewerPass123!", viewer.password_hash)
        assert passwords.verify("NewSecurePass123!", viewer.password_hash)
        assert all("NewSecurePass123!" not in str(a.new_value) for a in session.scalars(select(Audit)).all())


def test_admin_validates_accounts_and_machine_names(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'admin.sqlite').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        admin = create_user(session, "Admin", "admin@example.com", "OriginalPass123!", "ADMIN")
        with pytest.raises(ValueError, match="e-mail"):
            create_user(session, "Segundo", "ADMIN@example.com", "AnotherPass123!",
                        "OPERADOR", actor_role="ADMIN", actor_id=admin.id)
        with pytest.raises(ValueError, match="72 bytes"):
            create_user(session, "Segundo", "segundo@example.com", "A" * 73,
                        "OPERADOR", actor_role="ADMIN", actor_id=admin.id)
        first = create_machine(session, "Seccionadora 1", "CORTE", "", admin.id, "ADMIN")
        second = create_machine(session, "Seccionadora 2", "CORTE", "", admin.id, "ADMIN")
        with pytest.raises(ValueError, match="Já existe"):
            update_machine(session, second, first.name, "CORTE", "", admin.id, "ADMIN")
        update_machine(session, second, "Seccionadora 3", "FITAGEM", "Revisada",
                       admin.id, "ADMIN")
        assert second.name == "Seccionadora 3"
        assert second.kind == "FITAGEM"
        assert second.observation == "Revisada"
