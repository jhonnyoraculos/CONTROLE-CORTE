"""Service-side authorization for operational administration."""

import uuid
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from config.constants import ROLES
from db.models import (
    AppSetting,
    Audit,
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
from services.auth_service import passwords, require_role
from utils.logging import log_event


def _audit(session: Session, actor_id, entity: str, entity_id, field: str, old, new, action: str):
    session.add(Audit(user_id=actor_id, entity=entity, entity_id=str(entity_id),
                      field=field, old_value=None if old is None else str(old),
                      new_value=None if new is None else str(new), source="MANUAL", action=action))


def update_user(session: Session, target: User, actor_id: uuid.UUID, actor_role: str,
                role: str, active: bool, new_password: str = "") -> None:
    require_role(actor_role, "admin")
    if role not in ROLES:
        raise ValueError("Perfil inválido.")
    if target.id == actor_id and (role != "ADMIN" or not active):
        raise ValueError("Não altere seu próprio acesso de administrador.")
    if new_password and (len(new_password) < 12 or len(new_password.encode("utf-8")) > 72):
        raise ValueError("A senha precisa ter ao menos 12 caracteres e até 72 bytes.")
    for field, value in {"role": role, "active": active}.items():
        old = getattr(target, field)
        if old != value:
            setattr(target, field, value)
            _audit(session, actor_id, "usuarios", target.id, field, old, value, "USER_UPDATED")
    if new_password:
        target.password_hash = passwords.hash(new_password)
        _audit(session, actor_id, "usuarios", target.id, "password_hash", "[HASH]", "[HASH]", "PASSWORD_RESET")
    log_event("USER_UPDATED", user_id=actor_id)


def create_machine(session: Session, name: str, kind: str, observation: str,
                   actor_id: uuid.UUID, actor_role: str) -> Machine:
    require_role(actor_role, "admin")
    if not name.strip() or not kind.strip() or len(name.strip()) > 120 or len(kind.strip()) > 80:
        raise ValueError("Nome e tipo são obrigatórios.")
    if session.scalar(select(Machine.id).where(Machine.name == name.strip())) is not None:
        raise ValueError("Já existe uma máquina com este nome.")
    machine = Machine(name=name.strip(), kind=kind.strip(), observation=observation.strip() or None)
    session.add(machine)
    session.flush()
    _audit(session, actor_id, "maquinas", machine.id, "name", None, machine.name, "MACHINE_CREATED")
    return machine


def toggle_machine(session: Session, machine: Machine, actor_id: uuid.UUID, actor_role: str) -> None:
    require_role(actor_role, "admin")
    old = machine.active
    machine.active = not old
    _audit(session, actor_id, "maquinas", machine.id, "active", old, machine.active, "MACHINE_UPDATED")


def update_machine(session: Session, machine: Machine, name: str, kind: str,
                   observation: str, actor_id: uuid.UUID, actor_role: str) -> None:
    require_role(actor_role, "admin")
    if not name.strip() or not kind.strip() or len(name.strip()) > 120 or len(kind.strip()) > 80:
        raise ValueError("Nome e tipo são obrigatórios.")
    if session.scalar(select(Machine.id).where(
        Machine.name == name.strip(), Machine.id != machine.id
    )) is not None:
        raise ValueError("Já existe uma máquina com este nome.")
    for field, value in {"name": name.strip(), "kind": kind.strip(),
                         "observation": observation.strip() or None}.items():
        old = getattr(machine, field)
        if old != value:
            setattr(machine, field, value)
            _audit(session, actor_id, "maquinas", machine.id, field, old, value, "MACHINE_UPDATED")


def save_capacity(session: Session, day, process: str, metric: str, machine_id,
                  shift: str, limit: Decimal, actor_id: uuid.UUID, actor_role: str) -> None:
    require_role(actor_role, "admin")
    if process not in {"CORTE", "FITAGEM", "USINAGEM"} or metric not in {
        "chapas", "cortes", "metros_corte", "fita", "usinagens"
    } or limit < 0:
        raise ValueError("Capacidade inválida.")
    existing = session.scalar(select(Capacity).where(
        Capacity.day == day, Capacity.process == process, Capacity.metric == metric,
        Capacity.machine_id == machine_id, Capacity.shift == shift))
    if existing:
        old = existing.limit
        existing.limit = limit
        entity_id = existing.id
    else:
        existing = Capacity(day=day, process=process, metric=metric,
                            machine_id=machine_id, shift=shift, limit=limit)
        session.add(existing)
        session.flush()
        old = None
        entity_id = existing.id
    _audit(session, actor_id, "capacidades_producao", entity_id, "limit", old, limit, "CAPACITY_CHANGED")
    log_event("CAPACITY_CHANGED", user_id=actor_id)


def save_setting(session: Session, key: str, value: int, actor_id: uuid.UUID,
                 actor_role: str) -> None:
    require_role(actor_role, "admin")
    if key != "max_upload_mb" or not 1 <= value <= 200:
        raise ValueError("Configuração inválida.")
    setting = session.get(AppSetting, key)
    old = setting.value if setting else None
    if setting:
        setting.value = {"value": value}
    else:
        session.add(AppSetting(key=key, value={"value": value}))
    _audit(session, actor_id, "configuracoes", key, "value", old, value, "SETTING_CHANGED")


RESET_TABLES = (
    Audit,
    LegacyRecord,
    ImportError,
    PendingLink,
    OrderItem,
    MaterialIssue,
    Note,
    ProductionEvent,
    StatusHistory,
    Document,
    Service,
    ImportBatch,
    Order,
)


def operational_data_counts(session: Session) -> dict[str, int]:
    labels = {
        Order: "pedidos",
        Service: "servicos",
        OrderItem: "itens",
        Document: "documentos",
        ImportBatch: "lotes_importacao",
        PendingLink: "pendencias_vinculacao",
        MaterialIssue: "pendencias_material",
        ProductionEvent: "eventos_producao",
        Note: "observacoes",
        Audit: "auditoria",
        LegacyRecord: "historico_legado",
    }
    return {
        label: int(session.scalar(select(func.count()).select_from(model)) or 0)
        for model, label in labels.items()
    }


def reset_operational_data(session: Session, actor_id, actor_role: str,
                           confirmation: str) -> dict[str, int]:
    require_role(actor_role, "admin")
    if confirmation.strip().upper() != "ZERAR DADOS":
        raise ValueError("Digite ZERAR DADOS para confirmar.")
    before = operational_data_counts(session)
    for model in RESET_TABLES:
        session.execute(delete(model).execution_options(synchronize_session=False))
    log_event("OPERATIONAL_DATA_RESET", user_id=actor_id)
    return before
