"""Database-backed production statuses, shifts, and event transitions."""

import re

from sqlalchemy.orm import Session

from config.constants import PROCESS_EVENTS, PRODUCTION_STATUSES
from db.models import AppSetting, Audit
from services.auth_service import require_role


DEFAULT_ALLOWED_FROM = {
    "INICIAR_CORTE": ["PROGRAMADO", "LIBERADO_PRODUCAO", "AGUARDANDO_MATERIAL"],
    "FINALIZAR_CORTE": ["EM_CORTE"],
    "INICIAR_FITAGEM": ["AGUARDANDO_FITAGEM"],
    "FINALIZAR_FITAGEM": ["EM_FITAGEM"],
    "INICIAR_USINAGEM": ["AGUARDANDO_USINAGEM"],
    "FINALIZAR_PRODUCAO": ["EM_USINAGEM", "AGUARDANDO_USINAGEM", "EM_FITAGEM", "AGUARDANDO_FITAGEM"],
}


def _setting(session: Session, key: str):
    item = session.get(AppSetting, key)
    return item.value if item else None


def get_statuses(session: Session) -> list[str]:
    value = _setting(session, "production_statuses")
    return value if isinstance(value, list) and value else list(PRODUCTION_STATUSES)


def get_shifts(session: Session) -> list[str]:
    value = _setting(session, "shifts")
    return value if isinstance(value, list) and value else ["GERAL"]


def get_flow(session: Session) -> dict[str, dict]:
    value = _setting(session, "production_flow")
    if isinstance(value, dict) and all(event in value for event in PROCESS_EVENTS):
        return value
    return {event: {"from": statuses, "to": PROCESS_EVENTS[event]}
            for event, statuses in DEFAULT_ALLOWED_FROM.items()}


def _save(session: Session, key: str, value, actor_id) -> None:
    item = session.get(AppSetting, key)
    old = item.value if item else None
    if item:
        item.value = value
    else:
        session.add(AppSetting(key=key, value=value))
    session.add(Audit(user_id=actor_id, entity="configuracoes", entity_id=key,
                      field="value", old_value=str(old), new_value=str(value),
                      source="MANUAL", action="SETTING_CHANGED"))


def save_statuses(session: Session, statuses: list[str], actor_id, role: str) -> None:
    require_role(role, "admin")
    clean = list(dict.fromkeys(item.strip().upper() for item in statuses if item.strip()))
    if not set(PRODUCTION_STATUSES).issubset(clean) or any(
        not re.fullmatch(r"[A-Z0-9_]{2,40}", item) for item in clean
    ):
        raise ValueError("Preserve os status iniciais e use nomes em maiúsculas com sublinhado.")
    existing_flow = _setting(session, "production_flow")
    if isinstance(existing_flow, dict):
        used = {status for rule in existing_flow.values() if isinstance(rule, dict)
                for status in [rule.get("to"), *(rule.get("from") or [])]}
        if not used.issubset(clean):
            raise ValueError("Um status removido ainda é usado nas transições de produção.")
    _save(session, "production_statuses", clean, actor_id)


def save_shifts(session: Session, shifts: list[str], actor_id, role: str) -> None:
    require_role(role, "admin")
    clean = list(dict.fromkeys(item.strip() for item in shifts if item.strip()))
    if not clean or len(clean) > 30 or any(len(item) > 80 for item in clean):
        raise ValueError("Informe de 1 a 30 turnos com até 80 caracteres.")
    _save(session, "shifts", clean, actor_id)


def save_flow(session: Session, flow: dict[str, dict], actor_id, role: str) -> None:
    require_role(role, "admin")
    statuses = set(get_statuses(session))
    if set(flow) != set(PROCESS_EVENTS):
        raise ValueError("Todos os eventos precisam estar configurados.")
    for event, rule in flow.items():
        if not isinstance(rule, dict) or rule.get("to") not in statuses or not rule.get("from"):
            raise ValueError(f"Regra inválida para {event}.")
        if any(previous not in statuses for previous in rule["from"]):
            raise ValueError(f"Status anterior inválido para {event}.")
    _save(session, "production_flow", flow, actor_id)
