"""Verify the configured database without printing credentials."""

from sqlalchemy import inspect, text

from db.engine import make_engine


def main() -> None:
    engine = make_engine()
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
        names = inspect(connection).get_table_names()
        if "alembic_version" not in names:
            raise RuntimeError("Banco conectado, mas migrations ainda não foram executadas.")
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        print(f"Banco OK | tipo={engine.dialect.name} | migration={version} | tabelas={len(names)}")


if __name__ == "__main__":
    main()
