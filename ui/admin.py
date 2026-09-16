"""Role protected operational configuration and historical-file review."""

from datetime import date
from decimal import Decimal

import streamlit as st
from sqlalchemy import select

from config.constants import ROLES
from db.models import AppSetting, Capacity, Machine, User
from importers.legacy_controle import inspect_legacy, parse_legacy
from services.admin_service import (
    create_machine,
    operational_data_counts,
    reset_operational_data,
    save_capacity,
    save_setting,
    toggle_machine,
    update_machine,
    update_user,
)
from services.auth_service import create_user
from services.legacy_service import import_legacy
from services.status_flow import get_flow, get_shifts, get_statuses, save_flow, save_shifts, save_statuses
from utils.formatters import date_br


def _rows(data: dict[str, int], value_label: str) -> list[dict]:
    return [{"Tabela": key, value_label: value} for key, value in data.items()]


def render(factory, user):
    st.title("Administracao")
    users_tab, machines_tab, capacity_tab, legacy_tab, settings_tab, reset_tab = st.tabs(
        ["Usuarios", "Maquinas", "Capacidades", "Historico legado", "Configuracoes", "Zerar dados"]
    )
    with users_tab:
        with factory() as session:
            users = session.scalars(select(User).order_by(User.name)).all()
            st.dataframe(
                [{"Nome": u.name, "E-mail": u.email, "Perfil": u.role, "Ativo": u.active} for u in users],
                hide_index=True,
                use_container_width=True,
            )
            with st.form("new_user"):
                name = st.text_input("Nome")
                email = st.text_input("E-mail")
                password = st.text_input("Senha temporaria (minimo 12 caracteres, maximo 72 bytes)", type="password")
                role = st.selectbox("Perfil", ROLES)
                submitted = st.form_submit_button("Criar usuario")
            if submitted:
                try:
                    with factory.begin() as tx:
                        create_user(tx, name, email, password, role, actor_role=user.role, actor_id=user.id)
                    st.success("Usuario criado.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
            if users:
                selected = st.selectbox("Editar usuario", users, format_func=lambda u: f"{u.name} - {u.email}")
                new_role = st.selectbox("Novo perfil", ROLES, index=ROLES.index(selected.role))
                active = st.checkbox("Ativo", value=selected.active)
                new_password = st.text_input("Nova senha opcional", type="password")
                if st.button("Salvar usuario"):
                    try:
                        with factory.begin() as tx:
                            target = tx.get(User, selected.id)
                            update_user(tx, target, user.id, user.role, new_role, active, new_password)
                        st.success("Usuario atualizado.")
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
    with machines_tab:
        with factory() as session:
            machines = session.scalars(select(Machine).order_by(Machine.name)).all()
            st.dataframe(
                [{"Nome": m.name, "Tipo": m.kind, "Ativa": m.active, "Observacao": m.observation} for m in machines],
                hide_index=True,
            )
        with st.form("machine"):
            name = st.text_input("Nome da maquina")
            kind = st.text_input("Tipo de processo")
            observation = st.text_area("Observacao")
            save_machine = st.form_submit_button("Cadastrar maquina")
        if save_machine:
            try:
                with factory.begin() as tx:
                    create_machine(tx, name, kind, observation, user.id, user.role)
                st.success("Maquina cadastrada.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        if machines:
            target = st.selectbox("Alterar disponibilidade", machines, format_func=lambda m: m.name)
            edit_name = st.text_input("Editar nome", value=target.name, key=f"machine_name_{target.id}")
            edit_kind = st.text_input("Editar tipo", value=target.kind, key=f"machine_kind_{target.id}")
            edit_observation = st.text_area(
                "Editar observacao",
                value=target.observation or "",
                key=f"machine_observation_{target.id}",
            )
            if st.button("Salvar dados da maquina"):
                try:
                    with factory.begin() as tx:
                        update_machine(tx, tx.get(Machine, target.id), edit_name, edit_kind,
                                       edit_observation, user.id, user.role)
                    st.success("Maquina atualizada.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
            if st.button("Ativar / desativar maquina"):
                with factory.begin() as tx:
                    toggle_machine(tx, tx.get(Machine, target.id), user.id, user.role)
                st.rerun()
    with capacity_tab:
        with factory() as session:
            values = session.scalars(select(Capacity).order_by(Capacity.day.desc()).limit(100)).all()
            machines = session.scalars(select(Machine).where(Machine.active.is_(True)).order_by(Machine.name)).all()
            st.dataframe(
                [{"Data": date_br(c.day), "Processo": c.process, "Metrica": c.metric,
                  "Limite": c.limit, "Turno": c.shift} for c in values],
                hide_index=True,
            )
        with st.form("capacity"):
            day = st.date_input("Data", value=date.today(), format="DD/MM/YYYY")
            process = st.selectbox("Processo", ["CORTE", "FITAGEM", "USINAGEM"])
            metric = st.selectbox("Metrica", ["chapas", "cortes", "metros_corte", "fita", "usinagens"])
            machine_labels = {"Geral": None, **{m.name: m.id for m in machines}}
            machine_name = st.selectbox("Maquina", list(machine_labels))
            shift = st.text_input("Turno", value="GERAL")
            limit = st.number_input("Limite", min_value=0.0, value=100.0, step=1.0)
            save_capacity_clicked = st.form_submit_button("Salvar capacidade")
        if save_capacity_clicked:
            with factory.begin() as tx:
                save_capacity(tx, day, process, metric, machine_labels[machine_name],
                              shift, Decimal(str(limit)), user.id, user.role)
            st.success("Capacidade salva.")
            st.rerun()
    with legacy_tab:
        st.info("Migracao historica exige revisao de colunas e conciliacao antes de gravar.")
        file = st.file_uploader("Planilha de controle antigo", type="xlsx", key="legacy")
        if file:
            try:
                columns = inspect_legacy(file.getvalue())
                st.dataframe(columns, hide_index=True, use_container_width=True)
                mapping = {}
                for info in columns:
                    if str(info.get("header")) == "27":
                        choice = st.selectbox("A coluna '27' representa", ["Selecione", "cliente_nome", "ignorar"])
                        if choice != "Selecione":
                            mapping[info["column"]] = choice
                if mapping:
                    result = parse_legacy(file.getvalue(), mapping)
                    st.write(f"{len(result.rows)} linhas validas - {len(result.errors)} erros")
                    st.dataframe(result.preview, hide_index=True, use_container_width=True)
                    for warning in result.warnings[:10]:
                        st.warning(warning)
                    if st.button("Confirmar importacao historica", type="primary"):
                        with factory.begin() as tx:
                            imported = import_legacy(tx, file.name, file.getvalue(), mapping, user.id, user.role)
                        st.success(
                            f"{imported.status}: {imported.stored} linhas arquivadas; "
                            f"{imported.matched_orders} pedidos encontrados; "
                            f"{imported.needs_review} vinculos para revisao."
                        )
            except Exception as exc:
                st.error(f"Nao foi possivel validar o arquivo: {exc}")
    with settings_tab:
        st.caption("Valores operacionais sao registrados no banco.")
        with factory() as session:
            values = session.scalars(select(AppSetting).order_by(AppSetting.key)).all()
            st.dataframe([{"Chave": item.key, "Valor": item.value} for item in values], hide_index=True)
            configured = next((item for item in values if item.key == "max_upload_mb"), None)
        key = "max_upload_mb"
        initial = int(configured.value.get("value", 20)) if configured else 20
        value = st.number_input("Limite de upload (MB)", min_value=1, max_value=200, value=initial)
        if st.button("Salvar configuracao"):
            with factory.begin() as tx:
                save_setting(tx, key, value, user.id, user.role)
            st.success("Configuracao salva.")
        with factory() as session:
            statuses = get_statuses(session)
            shifts = get_shifts(session)
            flow = get_flow(session)
        st.markdown("#### Turnos")
        shift_text = st.text_area("Um turno por linha", value="\n".join(shifts), key="shift_config")
        if st.button("Salvar turnos"):
            try:
                with factory.begin() as tx:
                    save_shifts(tx, shift_text.splitlines(), user.id, user.role)
                st.success("Turnos salvos.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        st.markdown("#### Status de producao")
        status_text = st.text_area("Um status por linha", value="\n".join(statuses), height=300, key="status_config")
        if st.button("Salvar status"):
            try:
                with factory.begin() as tx:
                    save_statuses(tx, status_text.splitlines(), user.id, user.role)
                st.success("Status salvos.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        st.markdown("#### Transicoes de producao")
        st.caption("Informe os status de origem separados por virgula; o status de destino deve existir acima.")
        rows = [{"Evento": event, "Status anteriores": ", ".join(rule["from"]),
                 "Proximo status": rule["to"]} for event, rule in flow.items()]
        edited = st.data_editor(rows, hide_index=True, disabled=["Evento"], use_container_width=True, key="flow_config")
        if st.button("Salvar transicoes"):
            try:
                records = edited.to_dict("records") if hasattr(edited, "to_dict") else edited
                new_flow = {
                    row["Evento"]: {
                        "from": [part.strip().upper() for part in row["Status anteriores"].split(",") if part.strip()],
                        "to": row["Proximo status"].strip().upper(),
                    }
                    for row in records
                }
                with factory.begin() as tx:
                    save_flow(tx, new_flow, user.id, user.role)
                st.success("Transicoes salvas.")
                st.rerun()
            except (ValueError, AttributeError, KeyError) as exc:
                st.error(str(exc))
    with reset_tab:
        st.warning(
            "Esta acao apaga pedidos, servicos, PDFs, importacoes, pendencias, eventos, "
            "observacoes, auditoria e historico legado."
        )
        st.caption("Usuarios, maquinas, capacidades e configuracoes permanecem no banco.")
        previous = st.session_state.pop("reset_done_counts", None)
        if previous:
            st.success("Dados operacionais zerados.")
            st.dataframe(_rows(previous, "Registros apagados"), hide_index=True, use_container_width=True)
        with factory() as session:
            counts = operational_data_counts(session)
        st.dataframe(_rows(counts, "Registros atuais"), hide_index=True, use_container_width=True)
        with st.form("reset_operational_data"):
            confirmation = st.text_input("Digite ZERAR DADOS para confirmar")
            acknowledged = st.checkbox("Entendo que esta acao apaga os dados operacionais do banco atual.")
            submitted = st.form_submit_button("Zerar dados operacionais", type="primary")
        if submitted:
            if not acknowledged:
                st.error("Marque a confirmacao antes de zerar os dados.")
                return
            try:
                with factory.begin() as tx:
                    deleted = reset_operational_data(tx, user.id, user.role, confirmation)
                st.session_state["reset_done_counts"] = deleted
                st.rerun()
            except (PermissionError, ValueError) as exc:
                st.error(str(exc))
