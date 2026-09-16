"""Configuration and authorization are enforced by services, not UI visibility."""

from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
import pytest

from config.constants import PRODUCTION_STATUSES
from config.settings import normalize_database_url
from db.models import (
    AppSetting,
    Audit,
    Base,
    Capacity,
    Document,
    ImportBatch,
    ImportError,
    LegacyRecord,
    Machine,
    MaterialIssue,
    Note,
    Order,
    OrderItem,
    PendingLink,
    ProductionEvent,
    Service,
    StatusHistory,
    User,
)
from services.admin_service import (
    create_machine,
    operational_data_counts,
    reset_operational_data,
    save_capacity,
    save_setting,
    update_machine,
    update_user,
)
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


def test_admin_can_reset_operational_data_without_removing_configuration(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'reset.sqlite').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        admin = create_user(session, "Admin", "admin@example.com", "OriginalPass123!", "ADMIN")
        admin_id = admin.id
        machine = create_machine(session, "Seccionadora", "CORTE", "", admin.id, "ADMIN")
        save_capacity(session, date(2026, 1, 1), "CORTE", "chapas", machine.id,
                      "GERAL", Decimal("10"), admin.id, "ADMIN")
        save_setting(session, "max_upload_mb", 50, admin.id, "ADMIN")
        order = Order(codigo_interno="173368", codigo_interno_normalizado="173368")
        session.add(order)
        session.flush()
        service = Service(pedido_id=order.id, codigo_servico="20533164",
                          codigo_servico_normalizado="20533164", raw_data={})
        batch = ImportBatch(user_id=admin.id, kind="SERVICOS_XLSX", status="CONCLUIDO")
        session.add_all([service, batch])
        session.flush()
        document = Document(
            batch_id=batch.id,
            pedido_id=order.id,
            tipo_documento="CARRINHO_PDF",
            nome_arquivo="carrinho.pdf",
            hash_sha256="1" * 64,
            tamanho=100,
            usuario_importacao=admin.id,
            status="VINCULADO",
            original_bytes=b"%PDF-test",
        )
        session.add(document)
        session.flush()
        session.add_all([
            OrderItem(pedido_id=order.id, documento_id=document.id, numero_item="1",
                      codigo_produto="P1", descricao="Produto"),
            PendingLink(documento_id=document.id, pedido_pdf="173368", motivo="teste"),
            ImportError(documento_id=document.id, severity="AVISO", message="teste"),
            ProductionEvent(pedido_id=order.id, type="INICIAR_CORTE", user_id=admin.id),
            StatusHistory(pedido_id=order.id, new_status="EM_CORTE", user_id=admin.id),
            Note(pedido_id=order.id, user_id=admin.id, text="teste"),
            MaterialIssue(pedido_id=order.id, produto="MDF", quantidade=Decimal("1"),
                          motivo="teste", responsavel="Admin", registrado_por=admin.id),
            LegacyRecord(documento_id=document.id, row_number=1, codigo_pedido="173368",
                         normalized_data={}, raw_data={}),
        ])

    with Session(engine) as session, session.begin():
        before = reset_operational_data(session, admin_id, "ADMIN", "ZERAR DADOS")
        assert before["pedidos"] == 1
        assert before["documentos"] == 1

    with Session(engine) as session:
        assert all(value == 0 for value in operational_data_counts(session).values())
        assert session.scalar(select(User).where(User.email == "admin@example.com")) is not None
        assert session.scalar(select(Machine)) is not None
        assert session.scalar(select(Capacity)) is not None
        assert session.get(AppSetting, "max_upload_mb") is not None


def test_admin_reset_requires_confirmation_and_permission(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'reset_guard.sqlite').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        admin = create_user(session, "Admin", "admin@example.com", "OriginalPass123!", "ADMIN")
        viewer = create_user(session, "Viewer", "viewer@example.com", "ViewerPass123!",
                             "CONSULTA", actor_role="ADMIN", actor_id=admin.id)
        with pytest.raises(ValueError, match="ZERAR DADOS"):
            reset_operational_data(session, admin.id, "ADMIN", "apagar")
        with pytest.raises(PermissionError):
            reset_operational_data(session, viewer.id, "CONSULTA", "ZERAR DADOS")
