"""Order search and source-aware detail."""

import uuid
import re
from datetime import date, datetime
from decimal import Decimal

import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import defer

from config.constants import PROCESS_EVENTS
from db.models import Audit, Document, Machine, MaterialIssue, Note, Order, ProductionEvent, User
from db.repositories.orders import find_order, order_detail, search_orders
from importers.normalizers import normalize_identifier
from services.pedido_service import completeness, order_totals
from services.producao_service import durations, record_event, set_status
from services.status_flow import get_flow, get_statuses
from services.note_service import add_note
from utils.formatters import date_br, datetime_br, decimal_br, money_br


def _fmt(value):
    if value is None:
        return "—"
    if isinstance(value, datetime):
        return datetime_br(value)
    if isinstance(value, date):
        return date_br(value)
    return decimal_br(value) if isinstance(value, Decimal) else str(value)


def render(factory, user):
    code = st.query_params.get("pedido")
    if code:
        with factory() as session:
            order = find_order(session, code)
            if order:
                order = order_detail(session, order.id)
                _detail(session, order, user, factory)
                return
        st.error("Pedido não encontrado.")
        if st.button("Voltar"):
            del st.query_params["pedido"]
            st.rerun()
    st.title("Pedidos")
    term = st.text_input("Pesquisar pedido, serviço, carrinho, cliente, documento, cidade ou vendedor")
    page = st.number_input("Página", min_value=1, value=1, step=1)
    with factory() as session:
        rows, total = search_orders(session, term, limit=50, offset=(page - 1) * 50)
        st.caption(f"{total} pedidos encontrados")
        st.dataframe([{"Pedido": o.codigo_interno, "Cliente": o.cliente_pdf or "(aguardando carrinho)",
                       "Carrinho": o.carrinho, "Dados": o.status_dados,
                       "Produção": o.status_producao, "Previsão": _fmt(o.previsao_entrega),
                       "Prioridade": o.prioridade} for o in rows], hide_index=True,
                     use_container_width=True)
        if rows:
            selected = st.selectbox("Abrir pedido", [o.codigo_interno for o in rows])
            if st.button("Ver detalhes", type="primary"):
                st.query_params["pedido"] = selected
                st.rerun()


def _detail(session, order: Order, user, factory):
    if st.button("← Voltar para pedidos"):
        del st.query_params["pedido"]
        st.rerun()
    st.title(f"Pedido {order.codigo_interno}")
    st.subheader(order.cliente_pdf or "Cliente ainda não identificado no carrinho")
    st.caption(f"{order.status_dados.replace('_', ' ')} · {order.status_producao.replace('_', ' ')} · "
               f"Carrinho {_fmt(order.carrinho)}")
    totals = order_totals(order)
    cols = st.columns(6)
    for col, (label, value) in zip(cols, [
        ("Chapas", totals["chapas"]), ("Cortes", totals["cortes"]),
        ("Metros corte", totals["metros_lineares_corte"]), ("Peças", totals["pecas"]),
        ("Fita", totals["fita_aplicada"]), ("Usinagens", totals["usinagens"])]):
        col.metric(label, decimal_br(value))
    overview, services, products, production, logistics, documents, notes, history = st.tabs(
        ["Visão geral", "Serviços", "Produtos", "Produção", "Logística",
         "Documentos", "Observações", "Histórico"])
    with overview:
        st.markdown("#### Completude")
        st.write(" · ".join(f"{'✓' if okay else '○'} {label}" for label, okay in completeness(order).items()))
        if order.carrinho:
            current_pdf = session.scalar(select(Document).where(
                Document.pedido_id == order.id, Document.tipo_documento == "CARRINHO_PDF",
                Document.status == "VINCULADO").options(
                    defer(Document.original_bytes)).order_by(Document.data_importacao.desc()))
            pdf_code = (current_pdf.metadata_json or {}).get("order_code") if current_pdf else None
            if normalize_identifier(pdf_code) == order.codigo_interno_normalizado:
                st.success("Pedido/Nota do PDF validado por igualdade exata com o Código Interno.")
            else:
                st.info("PDF vinculado manualmente; confira a pendência e o histórico.")
        service_ref = re.search(r"CORTECLOUD\s*[:#-]?\s*(\d+)",
                                order.informacao_separacao_pdf or "", re.I)
        if service_ref:
            if any(s.codigo_servico_normalizado == service_ref.group(1) for s in order.services):
                st.success(f"Serviço/CorteCloud {service_ref.group(1)} compatível com o pedido.")
            else:
                st.warning(f"CorteCloud {service_ref.group(1)} não coincide com os serviços importados.")
        st.markdown("#### Carrinho PDF")
        st.write({"Cliente": order.cliente_pdf, "Código cliente": order.codigo_cliente_pdf,
                  "CNPJ/CPF": order.cliente_documento, "Contato": order.cliente_contato,
                  "Vendedor": order.vendedor_pdf, "Loja": order.loja_venda,
                  "Data do pedido": _fmt(order.data_pedido), "Total": money_br(order.valor_total),
                  "Peso": _fmt(order.peso),
                  "Quantidade total do PDF (unidades misturadas)": _fmt(order.quantidade_total_pdf)})
        st.markdown("#### Planilha de serviços")
        st.write({"Previsão": _fmt(order.previsao_entrega),
                  "Clientes informados": ", ".join(sorted({s.cliente_origem for s in order.services if s.cliente_origem}))})
    with services:
        for service in order.services:
            with st.container(border=True):
                st.markdown(f"**Serviço / CorteCloud {service.codigo_servico}** · PLANILHA SERVIÇOS")
                st.write({"Cliente origem": service.cliente_origem,
                          "Cliente final origem": service.cliente_final_origem,
                          "Linha": service.linha_producao, "Central": service.central,
                          "Chapas": decimal_br(service.chapas), "Cortes": decimal_br(service.cortes),
                          "Metros corte": decimal_br(service.metros_lineares_corte),
                          "Peças": decimal_br(service.pecas), "Fita": decimal_br(service.fita_aplicada),
                          "Usinagens": decimal_br(service.usinagens),
                          "Observação original": service.observacao_origem})
    with products:
        st.caption("CARRINHO PDF · valores e descrições como impressos")
        types = sorted({item.tipo_item for item in order.items})
        category = st.selectbox("Tipo de produto", ["Todos", *types],
                                key=f"product_type_{order.id}")
        product_search = st.text_input("Buscar descrição ou código",
                                       key=f"product_search_{order.id}").casefold().strip()
        visible_items = [item for item in order.items
                         if (category == "Todos" or item.tipo_item == category) and
                         (not product_search or product_search in item.descricao.casefold() or
                          product_search in item.codigo_produto.casefold())]
        st.dataframe([{"Item": i.numero_item, "Código": i.codigo_produto,
                       "Descrição": i.descricao, "Tipo": i.tipo_item, "NCM": i.ncm,
                       "Quantidade": decimal_br(i.quantidade), "UN": i.unidade,
                       "Peso": decimal_br(i.peso),
                       "Unitário": money_br(i.valor_unitario), "Total": money_br(i.valor_total)}
                      for i in visible_items], hide_index=True, use_container_width=True)
    with production:
        events = session.scalars(select(ProductionEvent).where(
            ProductionEvent.pedido_id == order.id).order_by(ProductionEvent.at)).all()
        machine = session.get(Machine, order.maquina_id) if order.maquina_id else None
        st.write({"Status": order.status_producao, "Programado em": _fmt(order.programado_em),
                  "Turno": order.turno, "Máquina": machine.name if machine else None,
                  "Prioridade": order.prioridade,
                  "Falta material": order.falta_material})
        st.write("Durações:", durations(events))
        st.dataframe([{"Quando": datetime_br(e.at), "Evento": e.type,
                       "Usuário": session.get(User, e.user_id).name if e.user_id else "Sistema",
                       "Máquina": session.get(Machine, e.machine_id).name if e.machine_id else None,
                       "Observação": e.observation}
                      for e in events], hide_index=True, use_container_width=True)
        if user.role in {"ADMIN", "GESTOR", "OPERADOR"}:
            st.markdown("#### Ações de produção")
            flow = get_flow(session)
            allowed = [event for event in PROCESS_EVENTS
                       if order.status_producao in flow[event]["from"] and
                       not (order.falta_material and event == "INICIAR_CORTE")]
            if allowed:
                confirmed = st.checkbox("Confirmo a ação neste pedido",
                                        key=f"production_confirm_{order.id}")
                action_labels = {
                    "INICIAR_CORTE": "Iniciar corte",
                    "FINALIZAR_CORTE": "Finalizar corte",
                    "INICIAR_FITAGEM": "Iniciar fitagem",
                    "FINALIZAR_FITAGEM": "Finalizar fitagem",
                    "INICIAR_USINAGEM": "Iniciar usinagem",
                    "FINALIZAR_PRODUCAO": "Finalizar produção",
                }
                columns = st.columns(3)
                for index, event in enumerate(allowed):
                    column = columns[index % 3]
                    if column.button(action_labels[event], disabled=not confirmed,
                                     key=f"production_{event}_{order.id}"):
                        try:
                            with factory.begin() as tx:
                                record_event(tx, tx.get(Order, order.id), event,
                                             user.id, user.role)
                            st.success("Evento registrado.")
                            st.rerun()
                        except (ValueError, PermissionError) as exc:
                            st.error(str(exc))
            else:
                st.caption("Não há ação de produção disponível no status atual.")
        issues = session.scalars(select(MaterialIssue).where(
            MaterialIssue.pedido_id == order.id).order_by(MaterialIssue.registrado_em.desc())).all()
        if issues:
            st.markdown("#### Falta de material")
            st.dataframe([{"Produto": i.produto, "Quantidade": i.quantidade,
                           "Motivo": i.motivo, "Responsável": i.responsavel,
                           "Previsão": date_br(i.previsao_solucao), "Status": i.status_pendencia,
                           "Resolvido em": datetime_br(i.resolvido_em)} for i in issues],
                         hide_index=True, use_container_width=True)
        if user.role in {"ADMIN", "GESTOR"}:
            st.markdown("#### Ajustar status")
            options = get_statuses(session)
            target = st.selectbox("Novo status", options,
                                  index=options.index(order.status_producao)
                                  if order.status_producao in options else 0,
                                  key=f"order_status_target_{order.id}")
            reason = st.text_input("Motivo ou observação", key=f"order_status_reason_{order.id}")
            confirmed = st.checkbox("Confirmo a alteração do status deste pedido",
                                    key=f"order_status_confirm_{order.id}")
            if st.button("Salvar status", disabled=not confirmed):
                try:
                    with factory.begin() as tx:
                        set_status(tx, tx.get(Order, order.id), target,
                                   user.id, user.role, reason)
                    st.success("Status atualizado.")
                    st.rerun()
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
    with logistics:
        st.caption("CARRINHO PDF / OPERAÇÃO")
        st.write({"Modalidade": order.modalidade, "Carga": _fmt(order.data_carregamento),
                  "Previsão": _fmt(order.previsao_entrega), "Remessa": order.remessa,
                  "Endereço": order.endereco, "Bairro": order.bairro,
                  "Cidade": order.cidade, "CEP": order.cep,
                  "Informações para separação": order.informacao_separacao_pdf})
    with documents:
        docs = session.scalars(select(Document).where(Document.pedido_id == order.id).options(
            defer(Document.original_bytes)).order_by(Document.data_importacao.desc())).all()
        # Service imports do not have a single order_id, so show their audit references.
        service_doc_ids = set(session.scalars(select(Audit.document_id).where(
            Audit.entity_id.in_([str(s.id) for s in order.services]), Audit.document_id.is_not(None))).all())
        for doc_id in service_doc_ids:
            doc = session.get(Document, doc_id, options=(defer(Document.original_bytes),))
            if doc and doc not in docs:
                docs.append(doc)
        st.dataframe([{"Arquivo": d.nome_arquivo, "Fonte": d.tipo_documento,
                       "Por": session.get(User, d.usuario_importacao).name if d.usuario_importacao else "Sistema",
                       "Importado em": datetime_br(d.data_importacao), "Status": d.status,
                       "SHA-256": d.hash_sha256} for d in docs], hide_index=True,
                     use_container_width=True)
    with notes:
        st.write("Observação da operação:", order.observacao_operacional or "—")
        st.write("Observação do PDF:", order.observacao_pdf or "—")
        entries = session.scalars(select(Note).where(Note.pedido_id == order.id).order_by(Note.at.desc())).all()
        st.dataframe([{"Quando": datetime_br(n.at), "Comentário": n.text} for n in entries], hide_index=True)
        if user.role != "CONSULTA":
            text = st.text_area("Adicionar comentário", key=f"new_note_{order.id}")
            if st.button("Salvar comentário") and text.strip():
                with factory.begin() as tx:
                    add_note(tx, tx.get(Order, order.id), user.id, user.role, text)
                st.rerun()
    with history:
        entity_ids = [str(order.id)] + [str(s.id) for s in order.services]
        audits = session.scalars(select(Audit).where(Audit.entity_id.in_(entity_ids)).order_by(
            Audit.at.desc()).limit(300)).all()
        st.dataframe([{"Quando": datetime_br(a.at),
                       "Usuário": session.get(User, a.user_id).name if a.user_id else "Sistema",
                       "Fonte": a.source, "Entidade": a.entity,
                       "Campo": a.field, "Antes": a.old_value, "Depois": a.new_value,
                       "Ação": a.action} for a in audits], hide_index=True, use_container_width=True)
