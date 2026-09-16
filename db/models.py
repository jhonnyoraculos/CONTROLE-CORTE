"""Relational domain model. Source owned fields remain separate."""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, LargeBinary,
    Numeric, String, Text, UniqueConstraint, Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def now() -> datetime:
    return datetime.now(timezone.utc)


JsonType = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    pass


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class User(Base, Timestamps):
    __tablename__ = "usuarios"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(30), default="CONSULTA")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Order(Base, Timestamps):
    __tablename__ = "pedidos"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    codigo_interno: Mapped[str] = mapped_column(String(80), unique=True)
    codigo_interno_normalizado: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    data_pedido: Mapped[date | None] = mapped_column(Date)
    carrinho: Mapped[str | None] = mapped_column(String(80), index=True)
    codigo_cliente_pdf: Mapped[str | None] = mapped_column(String(80))
    cliente_pdf: Mapped[str | None] = mapped_column(String(255), index=True)
    cliente_documento: Mapped[str | None] = mapped_column(String(30), index=True)
    cliente_contato: Mapped[str | None] = mapped_column(String(120))
    vendedor_pdf: Mapped[str | None] = mapped_column(String(180))
    loja_venda: Mapped[str | None] = mapped_column(String(120))
    tipo_ordem_venda: Mapped[str | None] = mapped_column(String(120))
    modalidade: Mapped[str | None] = mapped_column(String(80))
    remessa: Mapped[str | None] = mapped_column(String(80))
    endereco: Mapped[str | None] = mapped_column(Text)
    bairro: Mapped[str | None] = mapped_column(String(160))
    cidade: Mapped[str | None] = mapped_column(String(160), index=True)
    cep: Mapped[str | None] = mapped_column(String(30))
    peso: Mapped[Decimal | None] = mapped_column(Numeric(16, 3))
    valor_produtos: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    taxa_entrega: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    desconto: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    valor_total: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    quantidade_total_pdf: Mapped[Decimal | None] = mapped_column(Numeric(16, 3))
    condicao_pagamento: Mapped[str | None] = mapped_column(String(255))
    informacao_separacao_pdf: Mapped[str | None] = mapped_column(Text)
    observacao_pdf: Mapped[str | None] = mapped_column(Text)
    data_carregamento: Mapped[date | None] = mapped_column(Date, index=True)
    previsao_entrega: Mapped[date | None] = mapped_column(Date, index=True)
    prioridade: Mapped[str] = mapped_column(String(20), default="NORMAL")
    status_dados: Mapped[str] = mapped_column(String(40), default="AGUARDANDO_CARRINHO", index=True)
    status_producao: Mapped[str] = mapped_column(String(40), default="AGUARDANDO_DADOS", index=True)
    observacao_operacional: Mapped[str | None] = mapped_column(Text)
    falta_material: Mapped[bool] = mapped_column(Boolean, default=False)
    status_antes_material: Mapped[str | None] = mapped_column(String(40))
    programado_em: Mapped[date | None] = mapped_column(Date, index=True)
    turno: Mapped[str | None] = mapped_column(String(80))
    maquina_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("maquinas.id", ondelete="SET NULL"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"))
    services: Mapped[list["Service"]] = relationship(back_populates="order")
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order")
    material_issues: Mapped[list["MaterialIssue"]] = relationship(back_populates="order")


class Service(Base, Timestamps):
    __tablename__ = "servicos"
    __table_args__ = (UniqueConstraint("pedido_id", "codigo_servico_normalizado", name="uq_servico_pedido_codigo"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    pedido_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pedidos.id", ondelete="RESTRICT"), index=True)
    codigo_servico: Mapped[str] = mapped_column(String(80))
    codigo_servico_normalizado: Mapped[str] = mapped_column(String(80), index=True)
    cliente_origem: Mapped[str | None] = mapped_column(String(255))
    cliente_final_origem: Mapped[str | None] = mapped_column(String(255))
    vendedor_origem: Mapped[str | None] = mapped_column(String(180))
    enviado_producao: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    central: Mapped[str | None] = mapped_column(String(120))
    linha_producao: Mapped[str | None] = mapped_column(String(180))
    chapas: Mapped[Decimal | None] = mapped_column(Numeric(16, 3))
    cortes: Mapped[Decimal | None] = mapped_column(Numeric(16, 3))
    metros_lineares_corte: Mapped[Decimal | None] = mapped_column(Numeric(16, 3))
    pecas: Mapped[Decimal | None] = mapped_column(Numeric(16, 3))
    fita_aplicada: Mapped[Decimal | None] = mapped_column(Numeric(16, 3))
    usinagens: Mapped[Decimal | None] = mapped_column(Numeric(16, 3))
    corte_realizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fitagem_realizada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    usinagem_realizada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    previsao_entrega: Mapped[date | None] = mapped_column(Date)
    observacao_origem: Mapped[str | None] = mapped_column(Text)
    raw_data: Mapped[dict] = mapped_column(JsonType, default=dict)
    order: Mapped[Order] = relationship(back_populates="services")


class ImportBatch(Base):
    __tablename__ = "lotes_importacao"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default="INICIADO")


class Document(Base):
    __tablename__ = "documentos_importados"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("lotes_importacao.id", ondelete="SET NULL"))
    pedido_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pedidos.id", ondelete="SET NULL"), index=True)
    tipo_documento: Mapped[str] = mapped_column(String(40))
    nome_arquivo: Mapped[str] = mapped_column(String(255))
    hash_sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    tamanho: Mapped[int] = mapped_column(Integer)
    data_importacao: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    usuario_importacao: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(40))
    quantidade_registros: Mapped[int] = mapped_column(Integer, default=0)
    quantidade_criados: Mapped[int] = mapped_column(Integer, default=0)
    quantidade_atualizados: Mapped[int] = mapped_column(Integer, default=0)
    quantidade_ignorados: Mapped[int] = mapped_column(Integer, default=0)
    quantidade_erros: Mapped[int] = mapped_column(Integer, default=0)
    original_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    metadata_json: Mapped[dict] = mapped_column("metadata", JsonType, default=dict)


class OrderItem(Base):
    __tablename__ = "itens_pedido"
    __table_args__ = (UniqueConstraint("documento_id", "numero_item", "codigo_produto", name="uq_item_documento_num_codigo"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    pedido_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pedidos.id", ondelete="RESTRICT"), index=True)
    documento_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documentos_importados.id", ondelete="RESTRICT"), index=True)
    numero_item: Mapped[str] = mapped_column(String(30))
    codigo_produto: Mapped[str] = mapped_column(String(80))
    descricao: Mapped[str] = mapped_column(Text)
    ncm: Mapped[str | None] = mapped_column(String(30))
    peso: Mapped[Decimal | None] = mapped_column(Numeric(16, 3))
    unidade: Mapped[str | None] = mapped_column(String(30))
    quantidade: Mapped[Decimal | None] = mapped_column(Numeric(16, 3))
    valor_unitario: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    valor_total: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    tipo_item: Mapped[str] = mapped_column(String(40), default="OUTRO")
    raw_data: Mapped[dict] = mapped_column(JsonType, default=dict)
    order: Mapped[Order] = relationship(back_populates="items")


class PendingLink(Base):
    __tablename__ = "pendencias_vinculacao"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    documento_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documentos_importados.id", ondelete="RESTRICT"), unique=True)
    pedido_pdf: Mapped[str | None] = mapped_column(String(80), index=True)
    cliente_pdf: Mapped[str | None] = mapped_column(String(255))
    carrinho: Mapped[str | None] = mapped_column(String(80))
    motivo: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="AGUARDANDO_VINCULACAO")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"))


class ImportError(Base):
    __tablename__ = "erros_importacao"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    documento_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documentos_importados.id", ondelete="RESTRICT"))
    linha: Mapped[int | None] = mapped_column(Integer)
    severity: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)
    raw_data: Mapped[dict | None] = mapped_column(JsonType)


class Audit(Base):
    __tablename__ = "auditoria"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"))
    entity: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str] = mapped_column(String(80), index=True)
    field: Mapped[str] = mapped_column(String(100))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(80))
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documentos_importados.id", ondelete="SET NULL"))


class Machine(Base, Timestamps):
    __tablename__ = "maquinas"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    kind: Mapped[str] = mapped_column(String(80))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    observation: Mapped[str | None] = mapped_column(Text)


class Capacity(Base):
    __tablename__ = "capacidades_producao"
    __table_args__ = (UniqueConstraint("day", "process", "metric", "machine_id", "shift", name="uq_capacidade"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    day: Mapped[date] = mapped_column(Date, index=True)
    process: Mapped[str] = mapped_column(String(50))
    metric: Mapped[str] = mapped_column(String(50))
    limit: Mapped[Decimal] = mapped_column(Numeric(16, 3))
    machine_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("maquinas.id", ondelete="RESTRICT"))
    shift: Mapped[str] = mapped_column(String(80), default="GERAL")


class ProductionEvent(Base):
    __tablename__ = "eventos_producao"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    pedido_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pedidos.id", ondelete="RESTRICT"), index=True)
    type: Mapped[str] = mapped_column(String(60))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"))
    machine_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("maquinas.id", ondelete="SET NULL"))
    observation: Mapped[str | None] = mapped_column(Text)


class StatusHistory(Base):
    __tablename__ = "historico_status"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    pedido_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pedidos.id", ondelete="RESTRICT"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    old_status: Mapped[str | None] = mapped_column(String(40))
    new_status: Mapped[str] = mapped_column(String(40))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"))


class Note(Base):
    __tablename__ = "observacoes"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    pedido_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pedidos.id", ondelete="RESTRICT"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"))
    text: Mapped[str] = mapped_column(Text)


class MaterialIssue(Base):
    """A confirmed material shortage attached to one order."""

    __tablename__ = "pendencias_material"
    __table_args__ = (
        CheckConstraint("quantidade > 0", name="ck_pendencias_material_quantidade_positiva"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    pedido_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pedidos.id", ondelete="RESTRICT"), index=True)
    produto: Mapped[str] = mapped_column(String(255))
    quantidade: Mapped[Decimal] = mapped_column(Numeric(16, 3))
    motivo: Mapped[str] = mapped_column(Text)
    previsao_solucao: Mapped[date | None] = mapped_column(Date, index=True)
    responsavel: Mapped[str] = mapped_column(String(180))
    status_pendencia: Mapped[str] = mapped_column(String(30), default="FALTA_MATERIAL", index=True)
    registrado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    registrado_por: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"))
    resolvido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolvido_por: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"))
    resolucao_observacao: Mapped[str | None] = mapped_column(Text)
    order: Mapped[Order] = relationship(back_populates="material_issues")


class AppSetting(Base):
    __tablename__ = "configuracoes"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict] = mapped_column(JsonType)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class LegacyRecord(Base):
    """Historical source snapshot; never silently changes current production data."""

    __tablename__ = "registros_legados"
    __table_args__ = (UniqueConstraint("documento_id", "row_number", name="uq_registro_legado_linha"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    documento_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documentos_importados.id", ondelete="RESTRICT"), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    codigo_pedido: Mapped[str] = mapped_column(String(80), index=True)
    codigo_servico: Mapped[str | None] = mapped_column(String(80))
    pedido_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pedidos.id", ondelete="SET NULL"), index=True)
    requires_review: Mapped[bool] = mapped_column(Boolean, default=False)
    normalized_data: Mapped[dict] = mapped_column(JsonType, default=dict)
    raw_data: Mapped[dict] = mapped_column(JsonType, default=dict)


Index("ix_pedidos_criado", Order.created_at)
