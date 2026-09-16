"""Run the Streamlit entry point with an isolated migrated-shaped database."""

from pathlib import Path

from sqlalchemy import create_engine
from streamlit.testing.v1 import AppTest

from db.engine import make_engine
from db.models import Base, Document, ImportError, Order, Service
from services.auth_service import create_user


def test_app_starts_and_login_shows_navigation(tmp_path: Path, monkeypatch) -> None:
    url = f"sqlite:///{(tmp_path / 'app.sqlite').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    make_engine.cache_clear()
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    from sqlalchemy.orm import Session

    with Session(engine) as session, session.begin():
        create_user(session, "Test Admin", "admin@example.com", "SecurePass123!", "ADMIN")
        order = Order(codigo_interno="T1", codigo_interno_normalizado="T1")
        session.add(order)
        session.flush()
        session.add(Service(pedido_id=order.id, codigo_servico="S1",
                            codigo_servico_normalizado="S1", raw_data={}))
        document = Document(pedido_id=order.id, tipo_documento="CARRINHO_PDF",
                            nome_arquivo="amostra.pdf", hash_sha256="0" * 64,
                            tamanho=9, original_bytes=b"%PDF-test",
                            status="ERRO_IMPORTACAO", metadata_json={"warnings": ["teste"]})
        session.add(document)
        session.flush()
        session.add(ImportError(documento_id=document.id, severity="ERRO",
                                message="PDF de teste"))
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run(timeout=30)
    assert not app.exception
    assert any("Controle do Corte" in item.value for item in app.title)
    app.text_input[0].set_value("admin@example.com")
    app.text_input[1].set_value("SecurePass123!")
    app.button[0].click().run(timeout=30)
    assert not app.exception
    for module in ("orders", "production", "planning", "pending", "reports", "imports", "admin"):
        page = AppTest.from_string(
            "from ui import " + module + "\n"
            "from db.engine import session_factory\n"
            "from db.models import User\n"
            "from sqlalchemy import select\n"
            "with session_factory()() as session:\n"
            "    user = session.scalar(select(User))\n"
            + module + ".render(session_factory(), user)\n"
        ).run(timeout=30)
        assert not page.exception, module
        if module == "imports":
            prepare = next(button for button in page.button
                           if button.label == "Preparar arquivo de origem")
            prepare.click().run(timeout=30)
            assert not page.exception
    detail = AppTest.from_string(
        "from ui import orders\n"
        "from db.engine import session_factory\n"
        "from db.models import User, Order\n"
        "from db.repositories.orders import order_detail\n"
        "from sqlalchemy import select\n"
        "f = session_factory()\n"
        "with f() as session:\n"
        "    user = session.scalar(select(User))\n"
        "    order = order_detail(session, session.scalar(select(Order.id)))\n"
        "    orders._detail(session, order, user, f)\n"
    ).run(timeout=30)
    assert not detail.exception
    make_engine.cache_clear()
