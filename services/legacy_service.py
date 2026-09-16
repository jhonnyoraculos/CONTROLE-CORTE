"""Historical import stores reviewable snapshots without changing live orders."""

import hashlib
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Document, ImportBatch, ImportError, LegacyRecord, Order, Service, now
from importers.legacy_controle import parse_legacy
from services.auth_service import require_role
from services.importacao_service import upload_limit, validate_upload


@dataclass
class LegacyImportResult:
    status: str
    stored: int = 0
    matched_orders: int = 0
    needs_review: int = 0
    errors: int = 0


def import_legacy(session: Session, name: str, data: bytes, mapping: dict[str, str],
                  user_id: uuid.UUID, role: str) -> LegacyImportResult:
    require_role(role, "admin")
    name = validate_upload(name, data, ".xlsx", upload_limit(session))
    parsed = parse_legacy(data, mapping)
    if not parsed.rows:
        raise ValueError("Nenhuma linha histórica válida para importar.")
    digest = hashlib.sha256(data).hexdigest()
    if session.scalar(select(Document.id).where(Document.hash_sha256 == digest)):
        return LegacyImportResult("DUPLICADO")
    orders = {code: order_id for order_id, code in session.execute(
        select(Order.id, Order.codigo_interno_normalizado)).all()}
    services = {(order_id, code) for order_id, code in session.execute(
        select(Service.pedido_id, Service.codigo_servico_normalizado)).all()}
    batch = ImportBatch(user_id=user_id, kind="LEGADO_XLSX", status="CONCLUIDO", finished_at=now())
    session.add(batch)
    session.flush()
    doc = Document(batch_id=batch.id, tipo_documento="LEGADO_XLSX", nome_arquivo=name,
                   hash_sha256=digest, tamanho=len(data), original_bytes=data,
                   usuario_importacao=user_id, status="IMPORTADO_COM_ERROS" if parsed.errors else "IMPORTADO",
                   quantidade_registros=len(parsed.rows) + len(parsed.errors),
                   quantidade_criados=len(parsed.rows), quantidade_erros=len(parsed.errors),
                   metadata_json={"mapping": mapping, "warnings": parsed.warnings})
    session.add(doc)
    session.flush()
    for issue in parsed.errors:
        session.add(ImportError(documento_id=doc.id, linha=issue.get("row"),
                                severity="ERRO", message=issue.get("message", "Erro no legado"),
                                raw_data=json.loads(json.dumps(issue, default=str))))
    result = LegacyImportResult(status=doc.status, errors=len(parsed.errors))
    for row in parsed.rows:
        order_id = orders.get(row.order_code)
        review = bool(row.raw_data.get("_needs_order_review")) or not order_id
        if row.service_code and order_id and (order_id, row.service_code) not in services:
            review = True
        session.add(LegacyRecord(documento_id=doc.id, row_number=row.row_number,
                                 codigo_pedido=row.order_code, codigo_servico=row.service_code,
                                 pedido_id=order_id, requires_review=review,
                                 normalized_data=json.loads(json.dumps(row.fields, default=str)),
                                 raw_data=row.raw_data))
        result.stored += 1
        result.matched_orders += bool(order_id)
        result.needs_review += review
    return result
