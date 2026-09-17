"""Reviewable imports with cached parsing and compact previews."""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from html import escape

import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import defer

from db.models import Audit, Document, ImportError, Order, Service
from importers.excel_servicos import parse_excel
from importers.normalizers import normalize_identifier
from importers.pdf_carrinho import parse_pdf
from services.importacao_service import ImportService, SERVICE_FIELDS
from services.pedido_service import clear_spreadsheet_delivery_dates, set_delivery_date
from services.route_service import allowed_weekdays_for, check_delivery_route
from ui.styles import chips, metric_cards, page_header
from utils.formatters import date_br, datetime_br


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


def _excel_sample_rows() -> list[dict]:
    return [
        {
            "Linha": 2,
            "Pedido": "173368",
            "Servico": "20533164",
            "Cliente": "Cliente exemplo",
            "Chapas": "11",
            "Cortes": "282",
            "Previsao da planilha": "Somente referencia",
        },
        {
            "Linha": 3,
            "Pedido": "175771",
            "Servico": "23041038",
            "Cliente": "Cliente exemplo",
            "Chapas": "2",
            "Cortes": "7",
            "Previsao da planilha": "Nao define entrega",
        },
    ]


def _excel_preview_rows(parsed, limit: int = 30) -> list[dict]:
    return [
        {
            "Linha": row.row_number,
            "Pedido": row.order_code,
            "Servico": row.service_code,
            "Cliente": row.fields.get("cliente_origem"),
            "Chapas": row.fields.get("chapas"),
            "Cortes": row.fields.get("cortes"),
            "Previsao da planilha": date_br(row.fields.get("previsao_entrega")),
        }
        for row in parsed.rows[:limit]
    ]


def _pdf_sample_rows() -> list[dict]:
    return [
        {
            "Arquivo": "carrinho.pdf",
            "Pedido": "173368",
            "Carrinho": "109633",
            "Itens": 9,
            "Status": "Exemplo",
        }
    ]


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
    status_text = "Importacao concluida" if tone == "good" else "Importacao com aviso"
    st.markdown(
        f"""
        <div class="jr-import-done">
          <div class="jr-import-check">&#10003;</div>
          <div>
            <div class="jr-import-title">{escape(status_text)}</div>
            <div class="jr-import-subtitle">
              {escape(result.file)} foi processado. A entrega do pedido continua manual pela rota.
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    metric_cards([
        ("Pedidos novos", result.orders_created, "Criados no banco", "good"),
        ("Servicos criados", result.created, "Linhas gravadas", "good"),
        ("Atualizados", result.updated, "Servicos alterados", "neutral"),
        ("Sem mudanca", result.ignored, "Ja existiam ou aguardam revisao", "warn" if result.ignored else "neutral"),
    ])
    st.dataframe([
        {"Etapa": "Pedido", "Resultado": "Criado no banco", "Quantidade": result.orders_created},
        {"Etapa": "Servico", "Resultado": "Criado", "Quantidade": result.created},
        {"Etapa": "Servico", "Resultado": "Atualizado", "Quantidade": result.updated},
        {"Etapa": "Linha", "Resultado": "Sem mudanca ou revisao", "Quantidade": result.ignored},
    ], hide_index=True, use_container_width=True)
    if result.warnings:
        with st.expander("Avisos da importacao", expanded=False):
            for warning in result.warnings[:25]:
                st.warning(warning)
    if result.changes:
        with st.expander("Mudancas gravadas", expanded=False):
            st.dataframe(result.changes[:100], hide_index=True, use_container_width=True)


def _excel_tab(factory, user, show_header: bool = True) -> None:
    if show_header:
        page_header(
            "Importar servicos",
            "Envie a planilha geral, confira o resumo e confirme a gravacao.",
            "Importacoes",
            "Excel",
            "X",
        )
    previous = st.session_state.get("last_excel_import")
    if previous:
        _show_excel_result(previous["result"])
        upload_container = st.expander("Importar outra planilha", expanded=False)
    else:
        upload_container = st.container()
    with upload_container:
        uploaded = st.file_uploader(
            "Planilha geral de servicos",
            type="xlsx",
            key=f"excel_{st.session_state.get('excel_uploader_version', 0)}",
        )
    if not uploaded:
        if not previous:
            metric_cards([
                ("1", "Envie", "Selecione a planilha geral", "neutral"),
                ("2", "Revise", "Confira pedidos, servicos e avisos", "neutral"),
                ("3", "Confirme", "Grave apenas quando estiver pronto", "neutral"),
            ])
            st.markdown("#### Exemplo dos dados esperados")
            st.caption("A data da coluna Previsao Entrega e apenas referencia da planilha; a entrega oficial e manual.")
            st.dataframe(_excel_sample_rows(), hide_index=True, use_container_width=True)
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
        preview_rows = _excel_preview_rows(parsed)
        st.markdown("#### Amostra da planilha")
        st.caption("Confira algumas linhas antes de confirmar. A Previsao da planilha nao define entrega do pedido.")
        st.dataframe(preview_rows, hide_index=True, use_container_width=True)

        with st.expander("Ver detalhes da planilha", expanded=False):
            if changed_services:
                st.markdown("##### Alteracoes detectadas")
                st.dataframe(changed_services[:50], hide_index=True, use_container_width=True)
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
            st.session_state["excel_uploader_version"] = st.session_state.get("excel_uploader_version", 0) + 1
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
    title = "Carrinhos importados" if not errors else "Carrinhos importados com aviso"
    st.markdown(
        f"""
        <div class="jr-import-done">
          <div class="jr-import-check">&#10003;</div>
          <div>
            <div class="jr-import-title">{escape(title)}</div>
            <div class="jr-import-subtitle">
              O resultado abaixo mostra quais PDFs foram vinculados ao pedido e quais precisam de revisao.
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    metric_cards([
        ("Vinculados", linked, "Pedidos encontrados", "good"),
        ("Pendentes", pending, "Aguardam vinculo", "warn" if pending else "neutral"),
        ("Duplicados", duplicate, "Ja estavam no banco", "neutral"),
        ("Erros", errors, "Arquivos nao gravados", "bad" if errors else "good"),
    ])
    with st.expander("Resultado por arquivo", expanded=bool(errors or pending)):
        st.dataframe(outcomes, hide_index=True, use_container_width=True)


def _pdf_tab(factory, user, show_header: bool = True) -> None:
    if show_header:
        page_header(
            "Importar carrinhos",
            "Envie um ou varios PDFs; o sistema vincula automaticamente quando encontra o pedido.",
            "Importacoes",
            "PDF",
            "P",
        )
    previous = st.session_state.get("last_pdf_import")
    if previous:
        _show_pdf_outcomes(previous["outcomes"])
        upload_container = st.expander("Importar outros carrinhos", expanded=False)
    else:
        upload_container = st.container()
    with upload_container:
        files = st.file_uploader(
            "Carrinhos em PDF",
            type="pdf",
            accept_multiple_files=True,
            key=f"pdfs_{st.session_state.get('pdf_uploader_version', 0)}",
        )
    if not files:
        if not previous:
            metric_cards([
                ("1", "Envie", "Selecione um ou mais PDFs", "neutral"),
                ("2", "Confira", "Veja se estao vinculaveis", "neutral"),
                ("3", "Importe", "Grave todos em lote", "neutral"),
            ])
            st.markdown("#### Exemplo dos carrinhos")
            st.dataframe(_pdf_sample_rows(), hide_index=True, use_container_width=True)
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
        st.markdown("#### Amostra dos carrinhos")
        st.dataframe(rows, hide_index=True, use_container_width=True)

        all_warnings = [
            {"Arquivo": item.name, "Aviso": warning}
            for item in previews
            for warning in item.warnings[:3]
        ]
        if all_warnings:
            with st.expander("Avisos dos PDFs", expanded=False):
                st.dataframe(all_warnings, hide_index=True, use_container_width=True)

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
            st.session_state["pdf_uploader_version"] = st.session_state.get("pdf_uploader_version", 0) + 1
            st.rerun()
    except Exception as exc:
        _error(exc)


def _history_tab(factory, show_header: bool = True) -> None:
    if show_header:
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


def _delivery_panel(factory, user) -> None:
    today = date.today()
    horizon = today + timedelta(days=45)
    with factory() as session:
        manual_delivery_ids = set(session.scalars(select(Audit.entity_id).where(
            Audit.entity == "pedidos",
            Audit.field == "previsao_entrega",
            Audit.source == "ROTA",
        )).all())
        pending_candidates = session.scalars(select(Order).where(
            Order.carrinho.is_not(None),
            Order.cidade.is_not(None),
        ).order_by(Order.codigo_interno).limit(200)).all()
        pending = [
            order for order in pending_candidates
            if order.previsao_entrega is None or str(order.id) not in manual_delivery_ids
        ]
        upcoming_candidates = session.scalars(select(Order).where(
            Order.previsao_entrega.is_not(None),
            Order.previsao_entrega >= today - timedelta(days=7),
        ).order_by(Order.previsao_entrega, Order.codigo_interno).limit(80)).all()
        upcoming = [
            order for order in upcoming_candidates
            if str(order.id) in manual_delivery_ids
        ]
        non_manual_dates = [
            order for order in upcoming_candidates
            if str(order.id) not in manual_delivery_ids
        ]

    metric_cards([
        ("Sem entrega", len(pending), "Pedidos com carrinho e cidade", "warn" if pending else "good"),
        ("Ja definidos", len(upcoming), "Entregas recentes e futuras", "neutral"),
        ("Regra", "Rota", "Cidade precisa bater com a semana", "good"),
    ])
    cleanup_count = st.session_state.pop("spreadsheet_delivery_cleanup_count", None)
    if cleanup_count is not None:
        st.success(f"{cleanup_count} data(s) vindas da planilha foram limpas. Agora devem ser definidas manualmente.")
    if non_manual_dates:
        st.warning(
            f"{len(non_manual_dates)} pedido(s) tem data antiga nao confirmada manualmente. "
            "Eles nao contam mais como entrega definida."
        )
    cleanup_cols = st.columns([1, 3])
    with cleanup_cols[0]:
        if st.button(
            "Limpar datas da planilha",
            help="Remove apenas entregas preenchidas por importacoes antigas da planilha de servicos.",
            disabled=user.role == "CONSULTA",
            use_container_width=True,
        ):
            with factory.begin() as tx:
                cleaned = clear_spreadsheet_delivery_dates(tx, user.id, user.role)
            st.session_state["spreadsheet_delivery_cleanup_count"] = cleaned
            st.rerun()
    if not pending:
        st.info("Nao ha pedidos com carrinho e cidade aguardando data de entrega.")
    else:
        options = {f"{order.codigo_interno} - {order.cidade or 'sem cidade'} - {order.cliente_pdf or ''}": order.id
                   for order in pending}
        selected_labels = st.multiselect("Pedidos para definir entrega", list(options), max_selections=25)
        delivery_date = st.date_input("Data de entrega informada por quem esta fazendo",
                                      min_value=today - timedelta(days=30),
                                      max_value=horizon,
                                      value=today,
                                      format="DD/MM/YYYY")
        preview = []
        can_save_route = False
        for label in selected_labels:
            order = next(item for item in pending if item.id == options[label])
            check = check_delivery_route(order.cidade, delivery_date)
            allowed = allowed_weekdays_for(order.cidade)
            preview.append({
                "Pedido": order.codigo_interno,
                "Cidade": order.cidade,
                "Data": date_br(delivery_date),
                "Dia escolhido": check.weekday,
                "Pode salvar": "SIM" if check.ok else "NAO",
                "Rota encontrada": ", ".join(check.routes) if check.routes else "-",
                "Dias permitidos": ", ".join(allowed) if allowed else "Cidade nao encontrada",
                "Motivo": "OK para esta rota" if check.ok else check.message,
            })
        if preview:
            if all(row["Pode salvar"] == "SIM" for row in preview):
                can_save_route = True
                st.success("Data liberada pela rota para todos os pedidos selecionados.")
            else:
                st.error("Data bloqueada: pelo menos uma cidade nao pertence a rota deste dia.")
            st.dataframe(preview, hide_index=True, use_container_width=True)
        submitted = st.button(
            "Salvar datas de entrega",
            type="primary",
            disabled=not selected_labels or not can_save_route,
            use_container_width=True,
        )
        if submitted:
            if not selected_labels:
                st.error("Selecione ao menos um pedido.")
                return
            saved = []
            blocked = []
            with factory.begin() as tx:
                for label in selected_labels:
                    order = tx.get(Order, options[label])
                    try:
                        set_delivery_date(tx, order, delivery_date, user.id, user.role)
                        saved.append(order.codigo_interno)
                    except (PermissionError, ValueError) as exc:
                        blocked.append({"Pedido": order.codigo_interno, "Motivo": str(exc)})
            if saved:
                st.success(f"Entrega definida para {len(saved)} pedido(s): {', '.join(saved[:10])}.")
            if blocked:
                st.error("Alguns pedidos nao foram salvos porque nao batem com a rota.")
                st.dataframe(blocked, hide_index=True, use_container_width=True)
            if saved and not blocked:
                st.rerun()
    if upcoming:
        with st.expander("Entregas ja definidas", expanded=False):
            st.dataframe([
                {"Pedido": order.codigo_interno, "Cidade": order.cidade,
                 "Entrega": date_br(order.previsao_entrega), "Cliente": order.cliente_pdf}
                for order in upcoming
            ], hide_index=True, use_container_width=True)


def render(factory, user):
    from ui import operation_grid

    page_header(
        "Operacao do corte",
        "Importe arquivos, acompanhe pedidos, registre a producao e defina entregas pela rota.",
        "Fluxo principal",
        "Sistema ativo",
        "O",
    )
    with st.expander("Importar planilha de servicos", expanded=True):
        _excel_tab(factory, user, show_header=False)
    with st.expander("Importar carrinhos PDF", expanded=True):
        _pdf_tab(factory, user, show_header=False)
    operation_grid.render(factory, user)
    with st.expander("Entrega por rota", expanded=True):
        _delivery_panel(factory, user)
    with st.expander("Historico de importacao", expanded=False):
        _history_tab(factory, show_header=False)
