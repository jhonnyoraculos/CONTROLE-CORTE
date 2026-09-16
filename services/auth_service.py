"""Password verification and service side role checks."""

import re

from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.constants import PERMISSIONS, ROLES
from db.models import Audit, User
from utils.logging import log_event

passwords = CryptContext(schemes=["bcrypt"], deprecated="auto")


def require_role(role: str, action: str) -> None:
    if role not in PERMISSIONS.get(action, set()):
        raise PermissionError(f"O perfil {role} não pode executar {action}.")


def create_user(session: Session, name: str, email: str, password: str, role: str,
                actor_role: str | None = None, actor_id=None) -> User:
    if actor_role is not None:
        require_role(actor_role, "admin")
    elif session.scalar(select(User.id).limit(1)) is not None:
        raise PermissionError("O primeiro administrador já existe.")
    elif role != "ADMIN":
        raise ValueError("O primeiro usuário deve ser ADMIN.")
    if role not in ROLES or len(password) < 12 or len(password.encode("utf-8")) > 72:
        raise ValueError("Perfil inválido ou senha fora do limite de 12 caracteres a 72 bytes.")
    clean_name = name.strip()
    clean_email = email.strip().lower()
    if (not clean_name or len(clean_name) > 160 or len(clean_email) > 255 or
            not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", clean_email)):
        raise ValueError("Informe nome e e-mail válidos.")
    if session.scalar(select(User.id).where(User.email == clean_email)) is not None:
        raise ValueError("Já existe um usuário com este e-mail.")
    user = User(name=clean_name, email=clean_email,
                password_hash=passwords.hash(password), role=role)
    session.add(user)
    session.flush()
    session.add(Audit(user_id=actor_id, entity="usuarios", entity_id=str(user.id),
                      field="email", old_value=None, new_value=user.email,
                      source="MANUAL", action="USER_CREATED"))
    log_event("USER_CREATED", user_id=actor_id)
    return user


def authenticate(session: Session, email: str, password: str) -> User | None:
    user = session.scalar(select(User).where(User.email == email.strip().lower(), User.active.is_(True)))
    if user and passwords.verify(password, user.password_hash):
        return user
    return None
