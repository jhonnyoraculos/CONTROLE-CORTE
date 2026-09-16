"""Reviewable imports with cached parsing and compact previews."""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass

import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import defer

from db.models import Document, ImportError, Order, Service
from importers.excel_servicos import parse_excel
from importers.normalizers import normalize_identifier
from importers.pdf_carrinho import parse_pdf
from services.importacao_service import ImportService, SERVICE_FIELDS
from ui.styles import chips, metric_cards, page_header
from utils.formatters import datetime_br


@dataclass(frozen=True)
class PdfPreview:
    name: str
    digest: str
    order_code: str | None
    cart_code: str | None
    items: int
    warnings: list[str]
    status: str


def _error(exc: Exception):
    ref = uuid.uuid4().hex[:8]
    logging.exception("IMPORT_FAILED ref=%s", ref)
    st.error(f"Nao foi possivel processar o arquivo. Referencia: {ref}")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _short_digest(data: bytes) -> str:
    return _digest(data)[:12]


@st.cache_data(show_spinner=False, max_entries=8)
def _parse_excel_cached(data: bytes):
    return parse_excel(data)


@st.cache_data(show_spinner=False, max_entries=64)
def _parse_pdf_cached(data: bytes):
    return parse_pdf(data)


def _excel_preview(factory, parsed):
    clean_rows = [row for row in parsed.rows if not row.raw_data.get("_needs_order_review")]
    codes = {row.order_code for row in clean_rows}
    existing_orders = {}
    existing_services = {}
    if codes:
        with factory() as session:
            existing_orders = {
                order.codigo_interno_normalizado: order
                for order in session.scalars(
                    select(Order).where(Order.codigo_interno_normalizado.in_(codes))
                ).all()
            }
            order_ids = [order.id for order in existing_orders.values()]
            if order_ids:
                existing_services = {
                    (service.pedido_id, service.codigo_servico_normalizado): service
                    for service in session.scalars(
                        select(Service).where(Service.pedido_id.in_(order_ids))
                    ).all()
                }
    new_services = 0
    changed_services = []
    for row in clean_rows:
        order = existing_orders.get(row.order_code)
        service = existing_services.get((order.id, row.service_code)) if order else None
        if service is None:
            new_services += 1
            continue
        differences = [
            f"{key}: {getattr(service, key)} -> {value}"
            for key, value in row.fields.items()
            if key in SERVICE_FIELDS and getattr(service, key) != value
        ]
        if differences:
            changed_services.append({"Servico": row.service_code, "Mudancas": "; ".join(differences)})
    return clean_rows, existing_orders, new_services, changed_services


def _show_excel_result(result) -> None:
    tone = "good" if result.status in {"IMPORTADO", "IMPORTADO_COM_ERROS"} else "warn"
    metric_cards([
        ("Status", result.status, result.file, tone),
        ("Pedidos novos", result.orders_created, "Criados no banco", "good"),
        ("Servicos criados", result.created, "Linhas novas", "good"),
        ("Atualizados", result.updated, "Servicos alterados", "neutral"),
        ("Ignorados", result.ignored, "Sem mudanca ou revisao", "warn"),
    ])
    if result.warnings:
        with st.expander("Avisos da importacao", expanded=False):
            for warning in result.warnings[:25]:
                st.warning(warning)
    if result.changes:
        with st.expander("Mudancas gravadas", expanded=False):
            st.dataframe(result.changes[:100], hide_index=True, use_container_width=True)


def _excel_tab(factory, user) -> None:
    page_header(
        "Importar servicos",
        "Envie a planilha geral, confira o resumo e confirme a gravacao.",
        "Importacoes",
        "Excel",
        "X",
    )
    uploaded = st.file_uploader("Planilha geral de servicos", type="xlsx", key="excel")
    if not uploaded:
        metric_cards([
            ("1", "Envie", "Selecione a planilha geral", "neutral"),
            ("2", "Revise", "Confira pedidos, servicos e avisos", "neutral"),
            ("3", "Confirme", "Grave apenas quando estiver pronto", "neutral"),
        ])
        return

    data = uploaded.getvalue()
    file_key = f"{uploaded.name}:{_short_digest(data)}"
    try:
        with st.spinner("Lendo planilha..."):
            parsed = _parse_excel_cached(data)
        clean_rows, existing_orders, new_services, changed_services = _excel_preview(factory, parsed)
        manual_review = len(parsed.rows) - len(clean_rows)
        new_orders = len({row.order_code for row in clean_rows} - set(existing_orders))
        metric_cards([
            ("Servicos validos", len(parsed.rows), uploaded.name, "good" if parsed.rows else "warn"),
            ("Pedidos novos", new_orders, "Nao existem no banco", "good"),
            ("Servicos novos", new_services, "Serao criados", "good"),
            ("Alteracoes", len(changed_services), "Servicos ja existentes", "neutral"),
            ("Revisao manual", manual_review, "Linhas compostas ou pendentes", "warn" if manual_review else "neutral"),
            ("Erros", len(parsed.errors), "Linhas com problema", "bad" if parsed.errors else "good"),
        ])
        chips([
            (f"{len(parsed.warnings)} avisos", "warn" if parsed.warnings else "good"),
            (f"{len(parsed.errors)} erros", "bad" if parsed.errors else "good"),
            (f"{len(clean_rows)} linhas prontas", "good"),
        ])
        previous = st.session_state.get("last_excel_import")
        if previous and previous.get("key") == file_key:
            _show_excel_result(previous["result"])

        with st.expander("Ver detalhes da planilha", expanded=False):
            if changed_services:
                st.markdown("##### Alteracoes detectadas")
                st.dataframe(changed_services[:50], hide_index=True, use_container_width=True)
            preview_rows = [
                {
                    "Linha": row.row_number,
                    "Pedido": row.order_code,
                    "Servico": row.service_code,
                    "Cliente": row.fields.get("cliente_origem"),
                    "Chapas": row.fields.get("chapas"),
                    "Cortes": row.fields.get("cortes"),
                }
                for row in parsed.rows[:30]
            ]
            st.dataframe(preview_rows, hide_index=True, use_container_width=True)
            for warning in parsed.warnings[:12]:
                st.warning(warning)
            if parsed.errors:
                st.error(f"{len(parsed.errors)} linhas com erro")
                st.dataframe(parsed.errors[:25], hide_index=True, use_container_width=True)

        with st.form("confirm_excel_import"):
            force_excel = st.checkbox(
                "Reprocessar arquivo identico",
                disabled=user.role != "ADMIN",
                key="force_excel_form",
            )
            confirmed = st.form_submit_button("Confirmar importacao de servicos", type="primary")
        if confirmed:
            with st.spinner("Importando servicos..."):
                with factory.begin() as session:
                    result = ImportService(session, user.id, user.role).import_excel(
                        uploaded.name, data, force=force_excel
                    )
            st.session_state["last_excel_import"] = {"key": file_key, "result": result}
            st.rerun()
    except Exception as exc:
        _error(exc)


def _pdf_previews(factory, payloads: list[tuple[str, bytes]]) -> list[PdfPreview]:
    parsed_items = []
    hashes = []
    codes = []
    for name, data in payloads:
        digest = _digest(data)
        hashes.append(digest)
        try:
            parsed = _parse_pdf_cached(data)
            code = normalize_identifier(parsed.order_code)
            if code:
                codes.append(code)
            parsed_items.append((name, digest, parsed, None))
        except Exception as exc:
            parsed_items.append((name, digest, None, exc))

    existing_hashes = set()
    matched_codes = set()
    with factory() as session:
        if hashes:
            existing_hashes = set(session.scalars(
                select(Document.hash_sha256).where(Document.hash_sha256.in_(hashes))
            ).all())
        if codes:
            matched_codes = set(session.scalars(
                select(Order.codigo_interno_normalizado).where(Order.codigo_interno_normalizado.in_(codes))
            ).all())

    previews = []
    for name, digest, parsed, exc in parsed_items:
        if exc or parsed is None:
            previews.append(PdfPreview(name, digest, None, None, 0, [], "ERRO DE LEITURA"))
            continue
        code = normalize_identifier(parsed.order_code)
        if not parsed.raw_text.strip():
            status = "SEM TEXTO"
        elif digest in existing_hashes:
            status = "JA IMPORTADO"
        elif code and code in matched_codes:
            status = "VINCULAVEL"
        else:
            status = "PENDENTE"
        previews.append(PdfPreview(
            name=name,
            digest=digest,
            order_code=parsed.order_code,
            cart_code=parsed.cart_code,
            items=len(parsed.items),
            warnings=list(parsed.warnings),
            status=status,
        ))
    return previews


def _show_pdf_outcomes(outcomes: list[dict]) -> None:
    linked = sum(1 for item in outcomes if item.get("Resultado") == "VINCULADO")
    pending = sum(1 for item in outcomes if item.get("Resultado") == "PENDENTE")
    duplicate = sum(1 for item in outcomes if item.get("Resultado") == "DUPLICADO")
    errors = sum(1 for item in outcomes if item.get("Resultado") == "ERRO")
    metric_cards([
        ("Vinculados", linked, "Pedidos encontrados", "good"),
        ("Pendentes", pending, "Aguardam vinculo", "warn" if pending else "neutral"),
        ("Duplicados", duplicate, "Ja estavam no banco", "neutral"),
        ("Erros", errors, "Arquivos nao gravados", "bad" if errors else "good"),
    ])
    with st.expander("Resultado por arquivo", expanded=bool(errors or pending)):
        st.dataframe(outcomes, hide_index=True, use_container_width=True)


def _pdf_tab(factory, user) -> None:
    page_header(
        "Importar carrinhos",
        "Envie um ou varios PDFs; o sistema vincula automaticamente quando encontra o pedido.",
        "Importacoes",
        "PDF",
        "P",
    )
    files = st.file_uploader(
        "Carrinhos em PDF",
        type="pdf",
        accept_multiple_files=True,
        key="pdfs",
    )
    if not files:
        metric_cards([
            ("1", "Envie", "Selecione um ou mais PDFs", "neutral"),
            ("2", "Confira", "Veja se estao vinculaveis", "neutral"),
            ("3", "Importe", "Grave todos em lote", "neutral"),
        ])
        return

    payloads = [(file.name, file.getvalue()) for file in files]
    batch_key = hashlib.sha256(b"".join(data for _, data in payloads)).hexdigest()[:12]
    try:
        with st.spinner("Lendo PDFs..."):
            previews = _pdf_previews(factory, payloads)
        status_counts = {status: sum(1 for item in previews if item.status == status)
                         for status in {item.status for item in previews}}
        metric_cards([
            ("Arquivos", len(previews), "Selecionados", "neutral"),
            ("Vinculaveis", status_counts.get("VINCULAVEL", 0), "Pedido encontrado", "good"),
            ("Pendentes", status_counts.get("PENDENTE", 0), "Sem pedido na planilha", "warn"),
            ("Ja importados", status_counts.get("JA IMPORTADO", 0), "Mesmo arquivo", "neutral"),
            ("Com erro", status_counts.get("ERRO DE LEITURA", 0) + status_counts.get("SEM TEXTO", 0),
             "Revisar arquivo", "bad" if status_counts.get("ERRO DE LEITURA", 0) else "neutral"),
        ])
        rows = [
            {
                "Arquivo": item.name,
                "Pedido": item.order_code,
                "Carrinho": item.cart_code,
                "Itens": item.items,
                "Status": item.status,
            }
            for item in previews
        ]
        st.dataframe(rows, hide_index=True, use_container_width=True)

        all_warnings = [
            {"Arquivo": item.name, "Aviso": warning}
            for item in previews
            for warning in item.warnings[:3]
        ]
        if all_warnings:
            with st.expander("Avisos dos PDFs", expanded=False):
                st.dataframe(all_warnings, hide_index=True, use_container_width=True)

        previous = st.session_state.get("last_pdf_import")
        if previous and previous.get("key") == batch_key:
            _show_pdf_outcomes(previous["outcomes"])

        with st.form("confirm_pdf_import"):
            force_pdf = st.checkbox(
                "Reprocessar PDFs identicos",
                disabled=user.role != "ADMIN",
                key="force_pdf_form",
            )
            confirmed = st.form_submit_button("Confirmar importacao dos carrinhos", type="primary")
        if confirmed:
            outcomes = []
            with st.spinner("Importando carrinhos..."):
                for name, data in payloads:
                    try:
                        with factory.begin() as session:
                            result = ImportService(session, user.id, user.role).import_pdf(
                                name, data, force=force_pdf
                            )
                        outcomes.append({
                            "Arquivo": name,
                            "Pedido": result.order_code,
                            "Carrinho": result.cart_code,
                            "Resultado": result.status,
                            "Itens": result.items,
                            "Aviso": "; ".join(result.warnings[:2]),
                        })
                    except Exception as exc:
                        _error(exc)
                        outcomes.append({"Arquivo": name, "Resultado": "ERRO"})
            st.session_state["last_pdf_import"] = {"key": batch_key, "outcomes": outcomes}
            st.rerun()
    except Exception as exc:
        _error(exc)


def _history_tab(factory) -> None:
    page_header(
        "Historico de importacao",
        "Ultimos arquivos gravados, com detalhes sob demanda.",
        "Auditoria",
        "Historico",
        "H",
    )
    with factory() as session:
        docs = session.scalars(select(Document).options(
            defer(Document.original_bytes)).order_by(Document.data_importacao.desc()).limit(50)).all()
        metric_cards([
            ("Arquivos recentes", len(docs), "Ultimos 50 registros", "neutral"),
            ("Importados", sum(1 for doc in docs if doc.status in {"IMPORTADO", "VINCULADO"}), "Concluidos", "good"),
            ("Pendentes", sum(1 for doc in docs if "PEND" in doc.status or "AGUARDANDO" in doc.status),
             "Precisam revisao", "warn"),
            ("Com erro", sum(1 for doc in docs if "ERRO" in doc.status), "Falha de leitura", "bad"),
        ])
        st.dataframe([
            {
                "Arquivo": d.nome_arquivo,
                "Tipo": d.tipo_documento,
                "Quando": datetime_br(d.data_importacao),
                "Status": d.status,
                "Criados": d.quantidade_criados,
                "Atualizados": d.quantidade_atualizados,
                "Erros": d.quantidade_erros,
            }
            for d in docs
        ], hide_index=True, use_container_width=True)
        if not docs:
            return
        by_id = {str(doc.id): doc for doc in docs}
        with st.expander("Inspecionar arquivo", expanded=False):
            selected_id = st.selectbox(
                "Arquivo",
                list(by_id),
                format_func=lambda key: f"{by_id[key].nome_arquivo} - {datetime_br(by_id[key].data_importacao)}",
            )
            selected = by_id[selected_id]
            st.write({
                "SHA-256": selected.hash_sha256,
                "Registros": selected.quantidade_registros,
                "Ignorados": selected.quantidade_ignorados,
                "Metadados": selected.metadata_json,
            })
            issues = session.scalars(select(ImportError).where(
                ImportError.documento_id == selected.id).order_by(ImportError.linha)).all()
            if issues:
                st.dataframe([
                    {"Linha": issue.linha, "Tipo": issue.severity, "Mensagem": issue.message}
                    for issue in issues
                ], hide_index=True, use_container_width=True)
            if st.session_state.get("prepared_doc_id") != selected_id:
                st.session_state.pop("prepared_doc_id", None)
                st.session_state.pop("prepared_doc_bytes", None)
            if st.button("Preparar arquivo de origem"):
                st.session_state["prepared_doc_id"] = selected_id
                st.session_state["prepared_doc_bytes"] = selected.original_bytes
            if st.session_state.get("prepared_doc_id") == selected_id:
                st.download_button(
                    "Baixar arquivo de origem",
                    data=st.session_state["prepared_doc_bytes"],
                    file_name=selected.nome_arquivo,
                    mime="application/pdf" if selected.tipo_documento == "CARRINHO_PDF"
                    else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )


def render(factory, user):
    excel_tab, pdf_tab, history_tab = st.tabs(["Excel", "PDF", "Historico"])
    with excel_tab:
        _excel_tab(factory, user)
    with pdf_tab:
        _pdf_tab(factory, user)
    with history_tab:
        _history_tab(factory)
