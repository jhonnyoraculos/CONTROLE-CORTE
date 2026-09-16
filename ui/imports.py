"""Reviewable imports: parsing happens before the confirm action."""

import logging
import hashlib
import uuid

import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import defer

from db.models import Document, ImportError, Order, Service
from importers.excel_servicos import parse_excel
from importers.pdf_carrinho import parse_pdf
from importers.normalizers import normalize_identifier
from services.importacao_service import ImportService, SERVICE_FIELDS
from utils.formatters import datetime_br


def _error(exc: Exception):
    ref = uuid.uuid4().hex[:8]
    logging.exception("IMPORT_FAILED ref=%s", ref)
    st.error(f"Não foi possível processar o arquivo. Referência: {ref}")


def render(factory, user):
    st.title("Importações")
    excel_tab, pdf_tab, history_tab = st.tabs(["Serviços · Excel", "Carrinhos · PDF", "Histórico"])
    with excel_tab:
        uploaded = st.file_uploader("Planilha geral de serviços", type="xlsx", key="excel")
        if uploaded:
            data = uploaded.getvalue()
            try:
                parsed = parse_excel(data)
                st.success(f"{len(parsed.rows)} serviços válidos; {len(parsed.errors)} erros; {len(parsed.warnings)} avisos.")
                clean_rows = [row for row in parsed.rows if not row.raw_data.get("_needs_order_review")]
                codes = {row.order_code for row in clean_rows}
                with factory() as session:
                    existing_orders = {order.codigo_interno_normalizado: order for order in
                                       session.scalars(select(Order).where(
                                           Order.codigo_interno_normalizado.in_(codes))).all()}
                    existing_services = {(service.pedido_id, service.codigo_servico_normalizado): service
                                         for service in session.scalars(select(Service).where(
                                             Service.pedido_id.in_([order.id for order in existing_orders.values()]))).all()}
                    new_services = 0
                    changed_services = []
                    for row in clean_rows:
                        order = existing_orders.get(row.order_code)
                        service = existing_services.get((order.id, row.service_code)) if order else None
                        if service is None:
                            new_services += 1
                        else:
                            differences = [f"{key}: {getattr(service, key)} → {value}"
                                           for key, value in row.fields.items() if key in SERVICE_FIELDS
                                           and getattr(service, key) != value]
                            if differences:
                                changed_services.append({"Serviço": row.service_code,
                                                         "Mudanças": "; ".join(differences)})
                summary = st.columns(4)
                summary[0].metric("Pedidos novos", len(codes - set(existing_orders)))
                summary[1].metric("Serviços novos", new_services)
                summary[2].metric("Serviços alterados", len(changed_services))
                summary[3].metric("Revisão manual", len(parsed.rows) - len(clean_rows))
                if changed_services:
                    st.markdown("#### Alterações detectadas")
                    st.dataframe(changed_services[:50], hide_index=True, use_container_width=True)
                st.dataframe([{"Linha": r.row_number, "Pedido": r.order_code,
                               "Serviço": r.service_code, "Cliente origem": r.fields.get("cliente_origem"),
                               "Chapas": r.fields.get("chapas"), "Cortes": r.fields.get("cortes")}
                              for r in parsed.rows[:30]], hide_index=True, use_container_width=True)
                for warning in parsed.warnings[:10]:
                    st.warning(warning)
                if parsed.errors:
                    st.error(f"{len(parsed.errors)} linhas com erro")
                    st.dataframe(parsed.errors[:20], hide_index=True)
                force_excel = st.checkbox("Reprocessar arquivo idêntico (ADMIN)",
                                          disabled=user.role != "ADMIN", key="force_excel")
                if st.button("Confirmar importação de serviços", type="primary"):
                    with factory.begin() as session:
                        result = ImportService(session, user.id, user.role).import_excel(
                            uploaded.name, data, force=force_excel)
                    st.success(f"{result.status}: {result.orders_created} pedidos novos, {result.created} serviços criados, "
                               f"{result.updated} atualizados e {result.ignored} ignorados.")
                    for warning in result.warnings[:10]:
                        st.warning(warning)
                    if result.changes:
                        st.dataframe(result.changes[:100], hide_index=True)
            except Exception as exc:
                _error(exc)
    with pdf_tab:
        files = st.file_uploader("Arraste ou selecione um ou vários carrinhos", type="pdf",
                                 accept_multiple_files=True, key="pdfs")
        if files:
            previews = []
            with factory() as session:
                for file in files:
                    try:
                        data = file.getvalue()
                        parsed = parse_pdf(data)
                        duplicate = session.scalar(select(Document.id).where(
                            Document.hash_sha256 == hashlib.sha256(data).hexdigest()))
                        code = normalize_identifier(parsed.order_code)
                        matched = session.scalar(select(Order.id).where(
                            Order.codigo_interno_normalizado == code)) if code else None
                        result_label = ("ERRO DE LEITURA" if not parsed.raw_text.strip() else
                                        "JÁ IMPORTADO" if duplicate else
                                        "VINCULÁVEL" if matched else "PENDENTE")
                        previews.append({"Arquivo": file.name, "Pedido": parsed.order_code,
                                         "Carrinho": parsed.cart_code, "Itens": len(parsed.items),
                                         "Avisos": len(parsed.warnings), "Resultado": result_label})
                    except Exception:
                        previews.append({"Arquivo": file.name, "Pedido": None, "Carrinho": None,
                                         "Itens": 0, "Resultado": "Erro de leitura"})
            st.dataframe(previews, hide_index=True, use_container_width=True)
            force_pdf = st.checkbox("Reprocessar PDFs idênticos (ADMIN)",
                                    disabled=user.role != "ADMIN", key="force_pdf")
            if st.button("Confirmar importação dos carrinhos", type="primary"):
                outcomes = []
                for file in files:
                    try:
                        with factory.begin() as session:
                            result = ImportService(session, user.id, user.role).import_pdf(
                                file.name, file.getvalue(), force=force_pdf)
                        outcomes.append({"Arquivo": file.name, "Pedido": result.order_code,
                                         "Carrinho": result.cart_code, "Resultado": result.status,
                                         "Itens": result.items,
                                         "Aviso": "; ".join(result.warnings[:2])})
                    except Exception as exc:
                        _error(exc)
                        outcomes.append({"Arquivo": file.name, "Resultado": "ERRO"})
                st.dataframe(outcomes, hide_index=True, use_container_width=True)
    with history_tab:
        with factory() as session:
            docs = session.scalars(select(Document).options(
                defer(Document.original_bytes)).order_by(Document.data_importacao.desc()).limit(100)).all()
            st.dataframe([{"Arquivo": d.nome_arquivo, "Tipo": d.tipo_documento,
                           "Quando": datetime_br(d.data_importacao), "Status": d.status,
                           "Criados": d.quantidade_criados, "Atualizados": d.quantidade_atualizados,
                           "Erros": d.quantidade_erros} for d in docs], hide_index=True,
                         use_container_width=True)
            if docs:
                by_id = {str(doc.id): doc for doc in docs}
                selected_id = st.selectbox("Inspecionar importação", list(by_id),
                                           format_func=lambda key: (
                                               f"{by_id[key].nome_arquivo} · "
                                               f"{datetime_br(by_id[key].data_importacao)}"))
                selected = by_id[selected_id]
                st.write({"SHA-256": selected.hash_sha256,
                          "Registros": selected.quantidade_registros,
                          "Ignorados": selected.quantidade_ignorados,
                          "Metadados": selected.metadata_json})
                issues = session.scalars(select(ImportError).where(
                    ImportError.documento_id == selected.id).order_by(ImportError.linha)).all()
                if issues:
                    st.dataframe([{"Linha": issue.linha, "Tipo": issue.severity,
                                   "Mensagem": issue.message} for issue in issues],
                                 hide_index=True, use_container_width=True)
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
