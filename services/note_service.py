"""Order comments with service-side permission and audit."""

from sqlalchemy.orm import Session

from db.models import Audit, Note, Order


def add_note(session: Session, order: Order, user_id, role: str, text: str) -> Note:
    if role not in {"ADMIN", "GESTOR", "PLANEJAMENTO", "OPERADOR"}:
        raise PermissionError("Perfil sem permissão para comentar.")
    content = text.strip()
    if not content or len(content) > 5000:
        raise ValueError("Comentário vazio ou acima de 5.000 caracteres.")
    note = Note(pedido_id=order.id, user_id=user_id, text=content)
    session.add(note)
    session.flush()
    session.add(Audit(user_id=user_id, entity="pedidos", entity_id=str(order.id),
                      field="observacao", old_value=None, new_value=content,
                      source="MANUAL", action="NOTE_ADDED"))
    return note
