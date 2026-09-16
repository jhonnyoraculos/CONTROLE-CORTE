"""Transactional, source-aware imports and exact PDF/order matching."""

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, func, select, text as sql_text
from sqlalchemy.orm import Session

from config.settings import get_settings
from db.models import (
    AppSetting, Audit, Document, ImportBatch, ImportError, Order, OrderItem, PendingLink,
    Service, StatusHistory, User, now,
)
from importers.excel_servicos import parse_excel
from importers.normalizers import normalize_identifier
from importers.pdf_carrinho import parse_pdf
from services.auth_service import require_role
from utils.formatters import datetime_br
from utils.logging import log_event


SERVICE_FIELDS = {
    "cliente_origem", "cliente_final_origem", "vendedor_origem", "enviado_producao",
    "central", "linha_producao", "chapas", "cortes", "metros_lineares_corte",
    "pecas", "fita_aplicada", "usinagens", "corte_realizado_em",
    "fitagem_realizada_em", "usinagem_realizada_em", "previsao_entrega",
    "observacao_origem",
}
PDF_FIELDS = {
    "data_pedido", "carrinho", "codigo_cliente_pdf", "cliente_pdf",
    "cliente_documento", "cliente_contato", "vendedor_pdf", "loja_venda",
    "tipo_ordem_venda", "modalidade", "remessa", "endereco", "bairro", "cidade",
    "cep", "peso", "quantidade_total_pdf", "valor_produtos", "taxa_entrega", "desconto", "valor_total",
    "condicao_pagamento", "informacao_separacao_pdf", "observacao_pdf",
    "data_carregamento",
}
ITEM_FIELDS = {
    "numero_item", "codigo_produto", "descricao", "ncm", "peso", "unidade",
    "quantidade", "valor_unitario", "valor_total", "tipo_item",
}


@dataclass
class ImportResult:
    file: str
    status: str
    created: int = 0
    updated: int = 0
    ignored: int = 0
    orders_created: int = 0
    items: int = 0
    order_code: str | None = None
    cart_code: str | None = None
    document_id: uuid.UUID | None = None
    warnings: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)


def _jsonable(value):
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def _audit(session: Session, entity: str, entity_id: uuid.UUID, field_name: str,
           old, new, source: str, action: str, user_id, document_id) -> None:
    session.add(Audit(user_id=user_id, entity=entity, entity_id=str(entity_id),
                      field=field_name, old_value=None if old is None else str(old),
                      new_value=None if new is None else str(new), source=source,
                      action=action, document_id=document_id))


def _set_source_fields(session, obj, incoming: dict, allowed: set[str],
                       source: str, user_id, doc_id, changes: list[str],
                       clear_blank: bool = False) -> bool:
    changed = False
    for key, value in incoming.items():
        if key not in allowed or (value is None and not clear_blank):
            continue
        old = getattr(obj, key)
        if old != value:
            setattr(obj, key, value)
            _audit(session, obj.__tablename__, obj.id, key, old, value,
                   source, "UPDATE", user_id, doc_id)
            changes.append(f"{key}: {old} → {value}")
            changed = True
    return changed


def validate_upload(name: str, data: bytes, suffix: str, max_upload_mb: int) -> str:
    from pathlib import PurePath

    safe_name = "".join(char for char in PurePath(name.replace("\\", "/")).name
                        if char.isprintable() and char not in "/\\")[:255]
    if not safe_name.lower().endswith(suffix):
        raise ValueError(f"Selecione um arquivo {suffix}.")
    if not data or len(data) > max_upload_mb * 1024 * 1024:
        raise ValueError("Arquivo vazio ou acima do limite configurado.")
    if suffix == ".xlsx" and not data.startswith(b"PK\x03\x04"):
        raise ValueError("O arquivo não parece ser um Excel XLSX válido.")
    if suffix == ".pdf" and not data.startswith(b"%PDF-"):
        raise ValueError("O arquivo não parece ser um PDF válido.")
    return safe_name


def upload_limit(session: Session) -> int:
    setting = session.get(AppSetting, "max_upload_mb")
    if setting:
        try:
            value = int(setting.value["value"])
            if 1 <= value <= 200:
                return value
        except (ValueError, TypeError, KeyError):
            pass
    return get_settings().max_upload_mb


def _lock_key(session: Session, namespace: int, key: str) -> None:
    """Serialize identical imports/orders within a PostgreSQL transaction."""
    if session.bind.dialect.name == "postgresql":
        session.execute(sql_text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:key))"),
                        {"namespace": namespace, "key": key})


class ImportService:
    """Call inside ``with Session.begin()`` to keep each file atomic."""

    def __init__(self, session: Session, user_id: uuid.UUID | None, role: str):
        self.session = session
        self.user_id = user_id
        self.role = role

    def _max_upload_mb(self) -> int:
        return upload_limit(self.session)

    def _document(self, name: str, data: bytes, kind: str, force: bool):
        digest = hashlib.sha256(data).hexdigest()
        _lock_key(self.session, 1, digest)
        existing = self.session.scalar(select(Document).where(Document.hash_sha256 == digest))
        if existing and not force:
            return existing, False
        if existing:
            require_role(self.role, "reprocess")
            self.session.execute(delete(ImportError).where(ImportError.documento_id == existing.id))
            existing.data_importacao = now()
            existing.usuario_importacao = self.user_id
            existing.status = "PROCESSANDO"
            existing.quantidade_registros = 0
            existing.quantidade_criados = 0
            existing.quantidade_atualizados = 0
            existing.quantidade_ignorados = 0
            existing.quantidade_erros = 0
            return existing, True
        doc = Document(tipo_documento=kind, nome_arquivo=name, hash_sha256=digest,
                       tamanho=len(data), original_bytes=data, status="PROCESSANDO",
                       usuario_importacao=self.user_id)
        self.session.add(doc)
        self.session.flush()
        return doc, True

    def _duplicate_warning(self, doc: Document) -> str:
        user = self.session.get(User, doc.usuario_importacao) if doc.usuario_importacao else None
        who = user.name if user else "sistema"
        when = datetime_br(doc.data_importacao)
        if doc.status == "ERRO_IMPORTACAO":
            return f"Este arquivo já foi recebido em {when} por {who}, com erro de leitura."
        return f"Este arquivo já foi importado em {when} por {who}."

    def _failed_pdf(self, doc: Document, message: str, reference: str) -> ImportResult:
        batch = ImportBatch(user_id=self.user_id, kind="CARRINHO_PDF",
                            status="ERRO", finished_at=now())
        self.session.add(batch)
        self.session.flush()
        doc.batch_id = batch.id
        doc.status = "ERRO_IMPORTACAO"
        doc.quantidade_erros = 1
        doc.metadata_json = {"reference": reference, "warnings": [message]}
        self.session.add(ImportError(documento_id=doc.id, severity="ERRO", message=message))
        log_event("IMPORT_FAILED", user_id=self.user_id, document_id=doc.id,
                  reference=reference)
        return ImportResult(doc.nome_arquivo, "ERRO_LEITURA", document_id=doc.id,
                            warnings=[f"{message} Referência: {reference}"])

    def import_excel(self, name: str, data: bytes, force: bool = False) -> ImportResult:
        require_role(self.role, "import")
        name = validate_upload(name, data, ".xlsx", self._max_upload_mb())
        doc, process = self._document(name, data, "SERVICOS_XLSX", force)
        if not process:
            return ImportResult(name, "DUPLICADO", document_id=doc.id,
                                warnings=[self._duplicate_warning(doc)])
        log_event("IMPORT_STARTED", user_id=self.user_id, document_id=doc.id)
        parsed = parse_excel(data)
        if not parsed.rows:
            raise ValueError("A planilha não contém serviços válidos.")
        result = ImportResult(name, "IMPORTADO", document_id=doc.id,
                              warnings=list(parsed.warnings))
        batch = ImportBatch(user_id=self.user_id, kind="SERVICOS_XLSX", status="CONCLUIDO", finished_at=now())
        self.session.add(batch)
        self.session.flush()
        doc.batch_id = batch.id
        doc.metadata_json = {"warnings": parsed.warnings, "errors": parsed.errors}
        for issue in parsed.errors:
            self.session.add(ImportError(documento_id=doc.id, linha=issue.get("row"),
                                         severity="ERRO", message=issue.get("message", str(issue)),
                                         raw_data=_jsonable(issue)))
        affected_orders: set[uuid.UUID] = set()
        for row in sorted(parsed.rows, key=lambda entry: (entry.order_code, entry.service_code)):
            if row.raw_data.get("_needs_order_review"):
                result.ignored += 1
                result.warnings.append(
                    f"Linha {row.row_number}: Código Interno composto; aguarda revisão manual."
                )
                self.session.add(ImportError(documento_id=doc.id, linha=row.row_number,
                                             severity="AVISO", message="Código Interno composto; não foi criado pedido sintético.",
                                             raw_data=_jsonable(row.raw_data)))
                continue
            _lock_key(self.session, 2, row.order_code)
            order = self.session.scalar(select(Order).where(
                Order.codigo_interno_normalizado == row.order_code))
            if order is None:
                order = Order(codigo_interno=row.order_code,
                              codigo_interno_normalizado=row.order_code,
                              created_by=self.user_id, updated_by=self.user_id)
                self.session.add(order)
                self.session.flush()
                result.orders_created += 1
                _audit(self.session, "pedidos", order.id, "codigo_interno", None,
                       row.order_code, "PLANILHA_SERVICOS", "CREATE", self.user_id, doc.id)
            affected_orders.add(order.id)
            service = self.session.scalar(select(Service).where(
                Service.pedido_id == order.id,
                Service.codigo_servico_normalizado == row.service_code))
            if service is None:
                service = Service(pedido_id=order.id, codigo_servico=row.service_code,
                                  codigo_servico_normalizado=row.service_code,
                                  raw_data=_jsonable(row.raw_data))
                self.session.add(service)
                self.session.flush()
                _set_source_fields(self.session, service, row.fields, SERVICE_FIELDS,
                                   "PLANILHA_SERVICOS", self.user_id, doc.id,
                                   result.changes, clear_blank=True)
                _audit(self.session, "servicos", service.id, "codigo_servico", None,
                       row.service_code, "PLANILHA_SERVICOS", "CREATE", self.user_id, doc.id)
                result.created += 1
            else:
                changes = []
                if _set_source_fields(self.session, service, row.fields, SERVICE_FIELDS,
                                      "PLANILHA_SERVICOS", self.user_id, doc.id,
                                      changes, clear_blank=True):
                    service.raw_data = _jsonable(row.raw_data)
                    result.updated += 1
                    result.changes.extend(f"{row.service_code} {c}" for c in changes)
                else:
                    result.ignored += 1
        for order_id in affected_orders:
            order = self.session.get(Order, order_id)
            earliest = self.session.scalar(select(func.min(Service.previsao_entrega)).where(
                Service.pedido_id == order_id))
            if earliest != order.previsao_entrega:
                old = order.previsao_entrega
                order.previsao_entrega = earliest
                _audit(self.session, "pedidos", order.id, "previsao_entrega", old,
                       order.previsao_entrega, "PLANILHA_SERVICOS", "UPDATE", self.user_id, doc.id)
        doc.status = "IMPORTADO_COM_ERROS" if parsed.errors else "IMPORTADO"
        result.status = doc.status
        doc.quantidade_registros = len(parsed.rows) + len(parsed.errors)
        doc.quantidade_criados = result.created
        doc.quantidade_atualizados = result.updated
        doc.quantidade_ignorados = result.ignored
        doc.quantidade_erros = len(parsed.errors)
        # A previously unmatched PDF is linked only on an exact normalized code.
        pending = self.session.scalars(select(PendingLink).where(
            PendingLink.status == "AGUARDANDO_VINCULACAO")).all()
        for item in pending:
            match = self.session.scalar(select(Order).where(
                Order.codigo_interno_normalizado == item.pedido_pdf))
            if match:
                pdf_doc = self.session.get(Document, item.documento_id)
                if pdf_doc:
                    self._attach_pdf(pdf_doc, match, parse_pdf(pdf_doc.original_bytes), item)
        log_event("IMPORT_COMPLETED", user_id=self.user_id, document_id=doc.id)
        return result

    def import_pdf(self, name: str, data: bytes, force: bool = False) -> ImportResult:
        require_role(self.role, "import")
        name = validate_upload(name, data, ".pdf", self._max_upload_mb())
        doc, process = self._document(name, data, "CARRINHO_PDF", force)
        if not process:
            return ImportResult(name, "DUPLICADO", document_id=doc.id,
                                warnings=[self._duplicate_warning(doc)])
        log_event("IMPORT_STARTED", user_id=self.user_id, document_id=doc.id)
        try:
            parsed = parse_pdf(data)
        except Exception:
            reference = uuid.uuid4().hex[:8]
            logging.exception("PDF_PARSE_FAILED reference=%s", reference)
            return self._failed_pdf(doc, "Não foi possível ler o PDF.", reference)
        if not parsed.raw_text.strip():
            return self._failed_pdf(doc, "PDF sem texto selecionável; OCR não está disponível.",
                                    uuid.uuid4().hex[:8])
        result = ImportResult(name, "PENDENTE", order_code=parsed.order_code,
                              cart_code=parsed.cart_code, document_id=doc.id,
                              warnings=list(parsed.warnings))
        batch = ImportBatch(user_id=self.user_id, kind="CARRINHO_PDF", status="CONCLUIDO", finished_at=now())
        self.session.add(batch)
        self.session.flush()
        doc.batch_id = batch.id
        doc.metadata_json = {"order_code": parsed.order_code, "cart_code": parsed.cart_code,
                             "warnings": parsed.warnings}
        code = normalize_identifier(parsed.order_code)
        if code:
            _lock_key(self.session, 2, code)
        order = self.session.scalar(select(Order).where(Order.codigo_interno_normalizado == code)) if code else None
        if not order:
            doc.status = "AGUARDANDO_VINCULACAO"
            pending = self.session.scalar(select(PendingLink).where(PendingLink.documento_id == doc.id))
            if not pending:
                self.session.add(PendingLink(documento_id=doc.id, pedido_pdf=code,
                                             cliente_pdf=parsed.fields.get("cliente_pdf"),
                                             carrinho=parsed.cart_code,
                                             motivo="Código do pedido ausente na planilha de serviços." if code
                                             else "Pedido/Nota não identificado no PDF."))
            log_event("IMPORT_COMPLETED", user_id=self.user_id, document_id=doc.id)
            return result
        pending = self.session.scalar(select(PendingLink).where(PendingLink.documento_id == doc.id))
        self._attach_pdf(doc, order, parsed, pending)
        result.status = "VINCULADO"
        result.items = len(parsed.items)
        result.warnings = list((doc.metadata_json or {}).get("warnings", []))
        log_event("IMPORT_COMPLETED", user_id=self.user_id, document_id=doc.id)
        return result

    def _attach_pdf(self, doc: Document, order: Order, parsed, pending: PendingLink | None = None):
        fields = dict(parsed.fields)
        fields["carrinho"] = parsed.cart_code
        changes = []
        _set_source_fields(self.session, order, fields, PDF_FIELDS, "CARRINHO_PDF",
                           self.user_id, doc.id, changes)
        complete = bool(order.services and parsed.cart_code and order.cliente_pdf
                        and order.endereco and order.cidade and parsed.items)
        target_data_status = "DADOS_COMPLETOS" if complete else "DADOS_PARCIAIS"
        if order.status_dados != target_data_status:
            old = order.status_dados
            order.status_dados = target_data_status
            _audit(self.session, "pedidos", order.id, "status_dados", old,
                   order.status_dados, "CARRINHO_PDF", "UPDATE", self.user_id, doc.id)
        if complete and order.status_producao == "AGUARDANDO_DADOS":
            old = order.status_producao
            order.status_producao = "AGUARDANDO_PROGRAMACAO"
            self.session.add(StatusHistory(pedido_id=order.id, old_status=old,
                                           new_status=order.status_producao,
                                           user_id=self.user_id))
            _audit(self.session, "pedidos", order.id, "status_producao", old,
                   order.status_producao, "CARRINHO_PDF", "STATUS_CHANGED",
                   self.user_id, doc.id)
        order.updated_by = self.user_id
        previous_docs = self.session.scalars(select(Document).where(
            Document.pedido_id == order.id, Document.tipo_documento == "CARRINHO_PDF",
            Document.id != doc.id, Document.status == "VINCULADO")).all()
        for previous in previous_docs:
            old_cart = (previous.metadata_json or {}).get("cart_code")
            if old_cart and parsed.cart_code and old_cart != parsed.cart_code:
                meta = dict(doc.metadata_json or {})
                meta.setdefault("warnings", []).append(
                    f"Carrinho anterior {old_cart} substituído pelo carrinho {parsed.cart_code}."
                )
                doc.metadata_json = meta
            for old_item in self.session.scalars(select(OrderItem).where(
                OrderItem.documento_id == previous.id)).all():
                self.session.delete(old_item)
            previous.status = "SUBSTITUIDO"
        doc.pedido_id = order.id
        doc.status = "VINCULADO"
        log_event("PDF_LINKED", user_id=self.user_id, document_id=doc.id, order_id=order.id)
        existing = self.session.scalars(select(OrderItem).where(OrderItem.documento_id == doc.id)).all()
        for item in existing:
            self.session.delete(item)
        self.session.flush()
        for item in parsed.items:
            data = {key: value for key, value in item.items() if key in ITEM_FIELDS}
            self.session.add(OrderItem(pedido_id=order.id, documento_id=doc.id,
                                       raw_data=_jsonable(item), **data))
        doc.quantidade_registros = len(parsed.items)
        doc.quantidade_criados = len(parsed.items)
        if pending:
            pending.status = "VINCULADO"
            pending.resolved_at = now()
            pending.resolved_by = self.user_id
        service_code = re.search(r"CORTECLOUD\s*[:#-]?\s*(\d+)",
                                 order.informacao_separacao_pdf or "", re.I)
        if service_code and not any(s.codigo_servico_normalizado == service_code.group(1)
                                    for s in order.services):
            meta = dict(doc.metadata_json or {})
            meta.setdefault("warnings", []).append("CorteCloud informado no PDF não coincide com os serviços do pedido.")
            doc.metadata_json = meta

    def link_pending(self, pending_id: uuid.UUID, order_id: uuid.UUID) -> None:
        require_role(self.role, "link")
        pending = self.session.get(PendingLink, pending_id)
        order = self.session.get(Order, order_id)
        if not pending or not order or pending.status != "AGUARDANDO_VINCULACAO":
            raise ValueError("Pendência ou pedido indisponível.")
        doc = self.session.get(Document, pending.documento_id)
        _lock_key(self.session, 1, doc.hash_sha256)
        _lock_key(self.session, 2, order.codigo_interno_normalizado)
        self._attach_pdf(doc, order, parse_pdf(doc.original_bytes), pending)
        _audit(self.session, "pedidos", order.id, "vinculo_pdf", None,
               str(doc.id), "MANUAL", "MANUAL_LINK", self.user_id, doc.id)
        log_event("MANUAL_LINK", user_id=self.user_id, document_id=doc.id, order_id=order.id)
