"""Interactive first administrator creation: python -m db.seed."""

import argparse
import getpass

from db.engine import session_factory
from services.auth_service import create_user


def main():
    parser = argparse.ArgumentParser(description="Criar o primeiro administrador")
    parser.add_argument("--name", required=True)
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    password = getpass.getpass("Senha (mínimo 12 caracteres, máximo 72 bytes): ")
    with session_factory().begin() as session:
        create_user(session, args.name, args.email, password, "ADMIN")
    print("Administrador criado.")


if __name__ == "__main__":
    main()
