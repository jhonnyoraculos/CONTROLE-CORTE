"""Exact unmatched PDFs and operational exception lists."""

import uuid
from datetime import date

import streamlit as st
from sqlalchemy import func, select

from config.constants import PERMISSIONS
from db.models import Document, MaterialIssue, Order, PendingLink
from db.repositories.orders import find_order, search_orders
from services.importacao_service import ImportService
from services.material_service import possible_issue_candidates, register_issue, resolve_issue
from utils.formatters import date_br, datetime_br


def render(factory, user):
    st.title("Pendências")
    with factory() as session:
        pending_count = session.scalar(select(func.count()).select_from(PendingLink).where(
            PendingLink.status == "AGUARDANDO_VINCULACAO")) or 0
        pending = session.scalars(select(PendingLink).where(
            PendingLink.status == "AGUARDANDO_VINCULACAO").order_by(PendingLink.created_at.desc()).limit(100)).all()
        document_names = dict(session.execute(select(Document.id, Document.nome_arquivo).where(
            Document.id.in_([item.documento_id for item in pending]))).all()) if pending else {}
        awaiting_count = session.scalar(select(func.count()).select_from(Order).where(
            Order.status_dados == "AGUARDANDO_CARRINHO")) or 0
        awaiting = session.scalars(select(Order).where(Order.status_dados == "AGUARDANDO_CARRINHO").limit(100)).all()
        missing_count = session.scalar(select(func.count()).select_from(Order).where(
            Order.falta_material.is_(True))) or 0
        overdue_count = session.scalar(select(func.count()).select_from(Order).where(
            Order.previsao_entrega < date.today(),
            Order.status_producao.not_in(["ENTREGUE", "RETIRADO", "CANCELADO"]))) or 0
        cols = st.columns(4)
        for col, (label, value) in zip(cols, [("PDFs sem pedido", pending_count),
                                               ("Aguardando carrinho", awaiting_count),
                                               ("Falta material", missing_count),
                                               ("Atrasados", overdue_count)]):
            col.metric(label, value)
        st.markdown("#### PDFs aguardando vínculo")
        st.dataframe([{"Documento": document_names.get(p.documento_id, "Arquivo indisponível"),
                       "Pedido no PDF": p.pedido_pdf, "Cliente": p.cliente_pdf,
                       "Carrinho": p.carrinho, "Motivo": p.motivo,
                       "Importado em": datetime_br(p.created_at)} for p in pending], hide_index=True,
                     use_container_width=True)
        if pending_count > len(pending):
            st.caption("Mostrando as 100 pendências de vínculo mais recentes.")
        if pending and user.role in {"ADMIN", "GESTOR"}:
            selected = st.selectbox("Pendência para vincular", pending,
                                    format_func=lambda p: f"{p.pedido_pdf or 'Sem código'} · {p.carrinho}")
            term = st.text_input("Pesquisar pedido de destino")
            orders, _ = search_orders(session, term, limit=50)
            if orders:
                code = st.selectbox("Pedido de destino", [o.codigo_interno for o in orders])
                confirm = st.checkbox("Confirmo manualmente o vínculo deste PDF ao pedido selecionado")
                if st.button("Vincular PDF", disabled=not confirm):
                    with factory.begin() as tx:
                        ImportService(tx, user.id, user.role).link_pending(
                            selected.id, next(o.id for o in orders if o.codigo_interno == code))
                    st.success("PDF vinculado.")
                    st.rerun()
        st.markdown("#### Pedidos aguardando carrinho")
        st.dataframe([{"Pedido": o.codigo_interno, "Previsão": date_br(o.previsao_entrega)}
                      for o in awaiting], hide_index=True, use_container_width=True)
        if awaiting_count > len(awaiting):
            st.caption("Mostrando até 100 pedidos aguardando carrinho.")

        st.markdown("#### Possíveis faltas de material")
        st.caption("Indicações nas observações originais da planilha. Um operador deve confirmar a falta.")
        candidates = possible_issue_candidates(session)
        st.dataframe([{
            "Pedido": item["order_code"], "Serviço": item["service_code"],
            "Observação original": str(item["observation"])[:350],
        } for item in candidates], hide_index=True, use_container_width=True)

        st.markdown("#### Pendências de material confirmadas")
        open_issues = session.scalars(select(MaterialIssue).where(
            MaterialIssue.status_pendencia == "FALTA_MATERIAL"
        ).order_by(MaterialIssue.registrado_em.desc()).limit(100)).all()
        order_codes = {
            issue.pedido_id: session.get(Order, issue.pedido_id).codigo_interno
            for issue in open_issues
        }
        st.dataframe([{
            "Pedido": order_codes[issue.pedido_id], "Produto": issue.produto,
            "Quantidade": issue.quantidade, "Motivo": issue.motivo,
            "Previsão de solução": date_br(issue.previsao_solucao),
            "Responsável": issue.responsavel, "Registrado em": datetime_br(issue.registrado_em),
        } for issue in open_issues], hide_index=True, use_container_width=True)

        if user.role in PERMISSIONS["material"]:
            st.markdown("##### Confirmar falta de material")
            candidate_codes = [str(item["order_code"]) for item in candidates]
            with st.form("register_material_issue"):
                selected_code = st.selectbox(
                    "Pedido sinalizado", ["(informar outro pedido)"] + candidate_codes,
                )
                manual_code = st.text_input("Código do pedido (para outro pedido)")
                product = st.text_input("Produto em falta")
                amount = st.text_input("Quantidade em falta", value="1")
                reason = st.text_area("Motivo da falta")
                forecast = st.date_input("Previsão de solução", value=None, format="DD/MM/YYYY")
                responsible = st.text_input("Responsável", value=user.name)
                confirmed = st.checkbox("Confirmo que a falta de material foi verificada")
                submitted = st.form_submit_button("Registrar falta")
            if submitted:
                code = manual_code.strip() or (
                    selected_code if selected_code != "(informar outro pedido)" else ""
                )
                try:
                    if not confirmed:
                        raise ValueError("Confirme a verificação da falta de material.")
                    with factory.begin() as tx:
                        order = find_order(tx, code) if code else None
                        if order is None:
                            raise ValueError("Selecione ou informe um pedido existente.")
                        register_issue(
                            tx, order.id, produto=product, quantidade=amount, motivo=reason,
                            previsao_solucao=forecast, responsavel=responsible,
                            user_id=user.id, role=user.role,
                        )
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
                else:
                    st.success("Falta de material registrada. A produção está aguardando material.")
                    st.rerun()

            if open_issues:
                st.markdown("##### Resolver falta de material")
                by_id = {str(issue.id): issue for issue in open_issues}
                with st.form("resolve_material_issue"):
                    selected_id = st.selectbox(
                        "Pendência a resolver", list(by_id),
                        format_func=lambda key: (
                            f"Pedido {order_codes[by_id[key].pedido_id]} · "
                            f"{by_id[key].produto} · {by_id[key].quantidade}"
                        ),
                    )
                    resolution_note = st.text_area("Observação da solução")
                    resolution_confirmed = st.checkbox("Confirmo que o material está disponível")
                    resolved = st.form_submit_button("Resolver pendência")
                if resolved:
                    try:
                        if not resolution_confirmed:
                            raise ValueError("Confirme a disponibilidade do material.")
                        with factory.begin() as tx:
                            resolve_issue(
                                tx, uuid.UUID(selected_id), user_id=user.id,
                                role=user.role, observacao=resolution_note,
                            )
                    except (ValueError, PermissionError) as exc:
                        st.error(str(exc))
                    else:
                        st.success("Pendência resolvida.")
                        st.rerun()

        resolved_issues = session.scalars(select(MaterialIssue).where(
            MaterialIssue.status_pendencia == "RESOLVIDA"
        ).order_by(MaterialIssue.resolvido_em.desc()).limit(50)).all()
        if resolved_issues:
            st.markdown("##### Pendências resolvidas recentemente")
            st.dataframe([{
                "Pedido": session.get(Order, issue.pedido_id).codigo_interno,
                "Produto": issue.produto, "Quantidade": issue.quantidade,
                "Resolvido em": datetime_br(issue.resolvido_em),
                "Observação": issue.resolucao_observacao,
            } for issue in resolved_issues], hide_index=True, use_container_width=True)
