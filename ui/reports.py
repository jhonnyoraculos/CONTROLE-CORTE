"""Aggregated and paginated operational reports with page exports."""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from io import BytesIO, StringIO
from math import ceil
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
import streamlit as st
from openpyxl import Workbook
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from db.models import Capacity, Machine, Order, ProductionEvent, Service
from services.status_flow import get_statuses


FINISHED = ("PRODUCAO_FINALIZADA", "AGUARDANDO_CARREGAMENTO",
            "LIBERADO_CARREGAMENTO", "ENTREGUE", "RETIRADO")
CLOSED = ("ENTREGUE", "RETIRADO", "CANCELADO")
DATE_COLUMNS = {"Previsão de entrega": Order.previsao_entrega,
                "Programação": Order.programado_em, "Data do pedido": Order.data_pedido}
CAPACITY_METRICS = {"chapas": "chapas", "cortes": "cortes",
                    "metros_corte": "metros_lineares_corte", "fita": "fita_aplicada",
                    "usinagens": "usinagens"}
LOCAL_ZONE = ZoneInfo("America/Sao_Paulo")


@dataclass(frozen=True)
class ReportFilters:
    start: date
    end: date
    date_basis: str = "Previsão de entrega"
    status: str | None = None
    machine_id: object | None = None
    city: str = ""
    seller: str = ""
    customer: str = ""
    line: str = ""
    store: str = ""
    modality: str = ""
    central: str = ""


def _order_conditions(filters: ReportFilters, *, with_dates: bool = True) -> list:
    conditions = []
    if with_dates:
        conditions.append(DATE_COLUMNS[filters.date_basis].between(filters.start, filters.end))
    if filters.status:
        conditions.append(Order.status_producao == filters.status)
    if filters.machine_id:
        conditions.append(Order.maquina_id == filters.machine_id)
    if filters.city.strip():
        conditions.append(Order.cidade.ilike(f"%{filters.city.strip()}%"))
    if filters.seller.strip():
        pattern = f"%{filters.seller.strip()}%"
        conditions.append(or_(Order.vendedor_pdf.ilike(pattern),
                              Order.services.any(Service.vendedor_origem.ilike(pattern))))
    if filters.customer.strip():
        pattern = f"%{filters.customer.strip()}%"
        conditions.append(or_(Order.cliente_pdf.ilike(pattern),
                              Order.services.any(or_(Service.cliente_origem.ilike(pattern),
                                                     Service.cliente_final_origem.ilike(pattern)))))
    if filters.line.strip():
        conditions.append(Order.services.any(Service.linha_producao.ilike(
            f"%{filters.line.strip()}%")))
    if filters.store.strip():
        conditions.append(Order.loja_venda.ilike(f"%{filters.store.strip()}%"))
    if filters.modality.strip():
        conditions.append(Order.modalidade.ilike(f"%{filters.modality.strip()}%"))
    if filters.central.strip():
        conditions.append(Order.services.any(Service.central.ilike(
            f"%{filters.central.strip()}%")))
    return conditions


def _service_totals():
    return select(
        Service.pedido_id.label("pedido_id"),
        *[func.sum(func.coalesce(getattr(Service, name), 0)).label(name)
          for name in ("chapas", "cortes", "metros_lineares_corte", "pecas",
                       "fita_aplicada", "usinagens")],
        func.max(Service.vendedor_origem).label("vendedor_origem"),
        func.max(Service.cliente_origem).label("cliente_origem"),
    ).group_by(Service.pedido_id).subquery()


def _category_condition(category: str):
    if category == "Atrasados":
        return and_(Order.previsao_entrega < date.today(),
                    Order.status_producao.not_in(CLOSED))
    if category == "Falta de material":
        return Order.falta_material.is_(True)
    if category == "Finalizados":
        return Order.status_producao.in_(FINISHED)
    if category == "Abertos":
        return and_(Order.status_producao.not_in(FINISHED),
                    Order.status_producao != "CANCELADO")
    return None


def operational_summary(session: Session, filters: ReportFilters) -> dict[str, int]:
    overdue = _category_condition("Atrasados")
    row = session.execute(select(
        func.count(Order.id),
        func.sum(case((Order.status_producao.in_(FINISHED), 1), else_=0)),
        func.sum(case((_category_condition("Abertos"), 1), else_=0)),
        func.sum(case((overdue, 1), else_=0)),
        func.sum(case((Order.falta_material.is_(True), 1), else_=0)),
    ).where(*_order_conditions(filters))).one()
    return dict(zip(("total", "finalizados", "abertos", "atrasados", "falta_material"),
                    (int(value or 0) for value in row)))


def order_page(session: Session, filters: ReportFilters, category: str,
               page: int, size: int) -> tuple[int, list[dict]]:
    conditions = _order_conditions(filters)
    extra = _category_condition(category)
    if extra is not None:
        conditions.append(extra)
    total = session.scalar(select(func.count(Order.id)).where(*conditions)) or 0
    service = _service_totals()
    data = session.execute(select(
        Order.codigo_interno, Order.cliente_pdf, Order.vendedor_pdf, Order.cidade,
        Order.previsao_entrega, Order.programado_em, Order.status_producao,
        Order.falta_material, Order.valor_total, Machine.name,
        service.c.chapas, service.c.cortes, service.c.pecas,
        service.c.fita_aplicada, service.c.usinagens,
    ).outerjoin(service, service.c.pedido_id == Order.id)
        .outerjoin(Machine, Machine.id == Order.maquina_id)
        .where(*conditions).order_by(Order.previsao_entrega, Order.codigo_interno)
        .offset((page - 1) * size).limit(size)).all()
    rows = [{"Pedido": code, "Cliente": client or "", "Vendedor": seller or "",
             "Cidade": city or "", "Máquina": machine or "", "Previsão": forecast,
             "Programação": planned, "Status": status,
             "Falta material": "Sim" if missing else "Não", "Chapas": sheets or 0,
             "Cortes": cuts or 0, "Peças": pieces or 0, "Fita": tape or 0,
             "Usinagens": routing or 0, "Valor (R$)": amount or 0}
            for code, client, seller, city, forecast, planned, status, missing,
                amount, machine, sheets, cuts, pieces, tape, routing in data]
    return int(total), rows


def volume_page(session: Session, filters: ReportFilters, dimension: str,
                page: int, size: int) -> tuple[int, list[dict]]:
    service = _service_totals()
    labels = {
        "Máquina": func.coalesce(func.nullif(Machine.name, ""), "Não informada"),
        "Vendedor": func.coalesce(func.nullif(Order.vendedor_pdf, ""),
                                  func.nullif(service.c.vendedor_origem, ""), "Não informado"),
        "Cidade": func.coalesce(func.nullif(Order.cidade, ""), "Não informada"),
        "Cliente": func.coalesce(func.nullif(Order.cliente_pdf, ""),
                                 func.nullif(service.c.cliente_origem, ""), "Não informado"),
    }
    label = labels[dimension]
    grouped = select(
        label.label("grupo"), func.count(Order.id).label("pedidos"),
        *[func.sum(func.coalesce(getattr(service.c, name), 0)).label(name)
          for name in ("chapas", "cortes", "pecas", "fita_aplicada", "usinagens")],
    ).select_from(Order).outerjoin(service, service.c.pedido_id == Order.id)
    grouped = grouped.outerjoin(Machine, Machine.id == Order.maquina_id)\
        .where(*_order_conditions(filters)).group_by(label).subquery()
    total = session.scalar(select(func.count()).select_from(grouped)) or 0
    data = session.execute(select(grouped).order_by(grouped.c.pedidos.desc(), grouped.c.grupo)
                           .offset((page - 1) * size).limit(size)).all()
    rows = [{dimension: name, "Pedidos": count, "Chapas": sheets or 0,
             "Cortes": cuts or 0, "Peças": pieces or 0, "Fita": tape or 0,
             "Usinagens": routing or 0}
            for name, count, sheets, cuts, pieces, tape, routing in data]
    return int(total), rows


def _utc_window(start: date, end: date) -> tuple[datetime, datetime]:
    first = datetime.combine(start, time.min, LOCAL_ZONE).astimezone(timezone.utc)
    after = datetime.combine(end + timedelta(days=1), time.min, LOCAL_ZONE).astimezone(timezone.utc)
    return first, after


def _local_day(session: Session, column):
    if session.bind.dialect.name == "postgresql":
        return func.date(func.timezone("America/Sao_Paulo", column))
    return func.date(func.datetime(column, "-3 hours"))


def production_trend(session: Session, filters: ReportFilters,
                     frequency: str = "Diária") -> list[dict]:
    finished = select(ProductionEvent.pedido_id.label("pedido_id"),
                      func.min(ProductionEvent.at).label("finished_at"))\
        .where(ProductionEvent.type == "FINALIZAR_PRODUCAO")\
        .group_by(ProductionEvent.pedido_id).subquery()
    first, after = _utc_window(filters.start, filters.end)
    local_day = _local_day(session, finished.c.finished_at)
    service = _service_totals()
    data = session.execute(select(
        local_day, func.count(Order.id),
        func.sum(func.coalesce(service.c.chapas, 0)),
        func.sum(func.coalesce(service.c.cortes, 0)),
        func.sum(func.coalesce(service.c.pecas, 0)),
    )
                           .select_from(finished).join(Order, Order.id == finished.c.pedido_id)
                           .outerjoin(service, service.c.pedido_id == Order.id)
                           .where(finished.c.finished_at >= first,
                                  finished.c.finished_at < after,
                                  *_order_conditions(filters, with_dates=False))
                           .group_by(local_day).order_by(local_day)).all()
    buckets: dict[str, dict] = {}
    for raw_day, count, sheets, cuts, pieces in data:
        day = raw_day if isinstance(raw_day, date) else date.fromisoformat(str(raw_day))
        if frequency == "Semanal":
            year, week, _ = day.isocalendar()
            key = f"{year}-S{week:02d}"
        elif frequency == "Mensal":
            key = day.strftime("%Y-%m")
        else:
            key = day.isoformat()
        bucket = buckets.setdefault(key, {"Período": key, "Pedidos finalizados": 0,
                                          "Chapas": Decimal(0), "Cortes": Decimal(0),
                                          "Peças": Decimal(0)})
        bucket["Pedidos finalizados"] += count
        bucket["Chapas"] += sheets or 0
        bucket["Cortes"] += cuts or 0
        bucket["Peças"] += pieces or 0
    return [value for _, value in sorted(buckets.items())]


def average_times(session: Session, filters: ReportFilters) -> list[dict]:
    kinds = ("INICIAR_CORTE", "FINALIZAR_CORTE", "INICIAR_FITAGEM",
             "FINALIZAR_FITAGEM", "INICIAR_USINAGEM", "FINALIZAR_PRODUCAO")
    event = select(ProductionEvent.pedido_id.label("pedido_id"),
                   *[func.min(case((ProductionEvent.type == kind, ProductionEvent.at)))
                     .label(kind.lower()) for kind in kinds])\
        .group_by(ProductionEvent.pedido_id).subquery()
    pairs = (("Corte", event.c.iniciar_corte, event.c.finalizar_corte),
             ("Fitagem", event.c.iniciar_fitagem, event.c.finalizar_fitagem),
             ("Usinagem", event.c.iniciar_usinagem, event.c.finalizar_producao),
             ("Produção completa", event.c.iniciar_corte, event.c.finalizar_producao))
    columns = []
    for _, started, ended in pairs:
        valid = and_(started.is_not(None), ended.is_not(None), ended >= started)
        if session.bind.dialect.name == "postgresql":
            hours = func.extract("epoch", ended - started) / 3600.0
        else:
            hours = (func.julianday(ended) - func.julianday(started)) * 24.0
        columns.extend((func.count(case((valid, 1))), func.avg(case((valid, hours)))))
    first, after = _utc_window(filters.start, filters.end)
    result = session.execute(select(*columns).select_from(event)
                             .join(Order, Order.id == event.c.pedido_id)
                             .where(event.c.finalizar_producao >= first,
                                    event.c.finalizar_producao < after,
                                    *_order_conditions(filters, with_dates=False))).one()
    return [{"Etapa": label, "Pedidos medidos": int(result[index] or 0),
             "Tempo médio (h)": round(float(result[index + 1]), 2)
             if result[index + 1] is not None else None}
            for index, (label, _, _) in zip(range(0, len(result), 2), pairs)]


def capacity_page(session: Session, filters: ReportFilters,
                  page: int, size: int) -> tuple[int, list[dict]]:
    conditions = [Capacity.day.between(filters.start, filters.end)]
    if filters.machine_id:
        conditions.append(Capacity.machine_id == filters.machine_id)
    total = session.scalar(select(func.count(Capacity.id)).where(*conditions)) or 0
    data = session.execute(select(Capacity, Machine.name)
                           .outerjoin(Machine, Machine.id == Capacity.machine_id)
                           .where(*conditions)
                           .order_by(Capacity.day, Capacity.process, Capacity.metric, Machine.name)
                           .offset((page - 1) * size).limit(size)).all()
    if not data:
        return int(total), []
    service = _service_totals()
    first_day = min(row.day for row, _ in data)
    last_day = max(row.day for row, _ in data)
    usage = session.execute(select(
        Order.programado_em, Order.maquina_id, Order.turno,
        *[func.sum(func.coalesce(getattr(service.c, field), 0)).label(field)
          for field in CAPACITY_METRICS.values()],
    ).select_from(Order).outerjoin(service, service.c.pedido_id == Order.id)
        .where(Order.programado_em.between(first_day, last_day),
               Order.status_producao != "CANCELADO",
               *_order_conditions(filters, with_dates=False))
        .group_by(Order.programado_em, Order.maquina_id, Order.turno)).all()
    rows = []
    for capacity, machine in data:
        field = CAPACITY_METRICS.get(capacity.metric)
        if field is None:
            continue
        used = sum((record._mapping[field] or Decimal(0) for record in usage
                    if record.programado_em == capacity.day
                    and (capacity.machine_id is None or capacity.machine_id == record.maquina_id)
                    and (capacity.shift == "GERAL" or capacity.shift == record.turno)), Decimal(0))
        limit = capacity.limit or Decimal(0)
        rows.append({"Data": capacity.day, "Processo": capacity.process,
                     "Métrica": capacity.metric, "Máquina": machine or "Todas",
                     "Turno": capacity.shift, "Carga": used, "Limite": limit,
                     "Utilização (%)": round(float(used / limit * 100), 1) if limit else None,
                     "Excedida": "Sim" if used > limit else "Não"})
    return int(total), rows


MAX_EXPORT_ROWS = 20_000


def _downloads(rows: list[dict], name: str, scope: str = "desta página") -> None:
    if not rows:
        return
    columns = list(rows[0])
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, delimiter=";")
    writer.writeheader()
    writer.writerows(rows)
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("Relatório")
    sheet.append(columns)
    for row in rows:
        sheet.append([value if isinstance(value, (int, float, date, datetime)) else
                      str(value) if value is not None else "" for value in row.values()])
    stream = BytesIO()
    workbook.save(stream)
    left, right = st.columns(2)
    left.download_button(f"Baixar CSV {scope}", "\ufeff" + output.getvalue(),
                         file_name=f"{name}.csv", mime="text/csv", key=f"{name}_csv")
    right.download_button(f"Baixar XLSX {scope}", stream.getvalue(),
                          file_name=f"{name}.xlsx",
                          mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                          key=f"{name}_xlsx")


def _filtered_download(total: int, fetch, name: str, signature: object) -> None:
    if total <= 0:
        return
    if total > MAX_EXPORT_ROWS:
        st.warning(f"A exportação completa aceita até {MAX_EXPORT_ROWS:,} linhas. Reduza os filtros.")
        return
    digest = hashlib.sha256(repr(signature).encode("utf-8")).hexdigest()[:12]
    state_key = f"export_{name}_{digest}"
    if st.button("Preparar exportação de todos os resultados filtrados", key=f"prepare_{name}"):
        _, rows = fetch(1, total)
        st.session_state[state_key] = rows
    if state_key in st.session_state:
        _downloads(st.session_state[state_key], f"{name}_filtrado", "dos filtros")


def _pager(total: int, key: str) -> tuple[int, int]:
    left, right = st.columns([1, 2])
    size = left.selectbox("Linhas por página", (25, 50, 100), index=1, key=f"{key}_size")
    pages = max(1, ceil(total / size))
    page = right.number_input("Página", min_value=1, max_value=pages,
                              value=1, step=1, key=f"{key}_page")
    st.caption(f"{total} registros · página {page} de {pages}")
    return page, size


def render(factory, user) -> None:
    st.title("Relatórios")
    st.caption("Pedidos usam a data escolhida; produção e tempos usam a data real de conclusão.")
    with factory() as session:
        machines = session.execute(select(Machine.id, Machine.name)
                                   .where(Machine.active.is_(True)).order_by(Machine.name)).all()
        statuses = get_statuses(session)
    options = {"Todas": None, **{name: machine_id for machine_id, name in machines}}
    with st.expander("Filtros", expanded=True):
        a, b, c = st.columns(3)
        start = a.date_input("De", date.today() - timedelta(days=30),
                             format="DD/MM/YYYY", key="report_start")
        end = b.date_input("Até", date.today(), format="DD/MM/YYYY", key="report_end")
        basis = c.selectbox("Data dos pedidos", list(DATE_COLUMNS), key="report_basis")
        d, e, f = st.columns(3)
        status = d.selectbox("Status", ("Todos", *statuses), key="report_status")
        machine = e.selectbox("Máquina", list(options), key="report_machine")
        city = f.text_input("Cidade contém", key="report_city")
        g, h = st.columns(2)
        seller = g.text_input("Vendedor contém", key="report_seller")
        customer = h.text_input("Cliente contém", key="report_customer")
        i, j, k, l = st.columns(4)
        line = i.text_input("Linha contém", key="report_line")
        store = j.text_input("Loja contém", key="report_store")
        modality = k.text_input("Modalidade contém", key="report_modality")
        central = l.text_input("Central contém", key="report_central")
    if start > end:
        st.error("A data inicial deve ser anterior ou igual à data final.")
        return
    filters = ReportFilters(start, end, basis, None if status == "Todos" else status,
                            options[machine], city, seller, customer, line, store,
                            modality, central)
    orders_tab, production_tab, volume_tab, capacity_tab = st.tabs(
        ("Pedidos", "Produção e tempos", "Volume por grupo", "Capacidade"))
    with factory() as session:
        with orders_tab:
            summary = operational_summary(session, filters)
            labels = (("Pedidos", "total"), ("Abertos", "abertos"),
                      ("Finalizados", "finalizados"), ("Atrasados", "atrasados"),
                      ("Falta de material", "falta_material"))
            for column, (label, key) in zip(st.columns(5), labels):
                column.metric(label, summary[key])
            category = st.selectbox("Exibir", ("Todos", "Abertos", "Finalizados",
                                                 "Atrasados", "Falta de material"))
            conditions = _order_conditions(filters)
            extra = _category_condition(category)
            if extra is not None:
                conditions.append(extra)
            total = session.scalar(select(func.count(Order.id)).where(*conditions)) or 0
            page, size = _pager(total, "orders_report")
            _, rows = order_page(session, filters, category, page, size)
            st.dataframe(rows, hide_index=True, use_container_width=True)
            _downloads(rows, "pedidos")
            if total > len(rows):
                _filtered_download(total, lambda p, s: order_page(session, filters, category, p, s),
                                   "pedidos", (filters, category))
        with production_tab:
            st.caption("Cada pedido é contado uma vez, na primeira conclusão registrada.")
            frequency = st.radio("Agrupar por", ("Diária", "Semanal", "Mensal"), horizontal=True)
            trend = production_trend(session, filters, frequency)
            if trend:
                figure = go.Figure(go.Bar(x=[row["Período"] for row in trend],
                                          y=[row["Pedidos finalizados"] for row in trend],
                                          marker_color="#327d90"))
                figure.update_layout(height=330, margin=dict(l=20, r=20, t=20, b=70))
                st.plotly_chart(figure, use_container_width=True)
                st.dataframe(trend, hide_index=True, use_container_width=True)
                _downloads(trend, "producao_por_periodo")
            else:
                st.info("Nenhuma conclusão registrada no período.")
            st.markdown("#### Tempo médio até a conclusão")
            averages = average_times(session, filters)
            st.dataframe(averages, hide_index=True, use_container_width=True)
            _downloads(averages, "tempos_medios")
        with volume_tab:
            dimension = st.selectbox("Agrupar volume por", ("Máquina", "Vendedor", "Cidade", "Cliente"))
            total, _ = volume_page(session, filters, dimension, 1, 1)
            page, size = _pager(total, "volume_report")
            _, rows = volume_page(session, filters, dimension, page, size)
            st.dataframe(rows, hide_index=True, use_container_width=True)
            _downloads(rows, "volume_por_grupo")
            if total > len(rows):
                _filtered_download(total, lambda p, s: volume_page(session, filters, dimension, p, s),
                                   "volume_por_grupo", (filters, dimension))
        with capacity_tab:
            st.caption("Carga programada e limite configurado por processo, máquina e turno.")
            capacity_conditions = [Capacity.day.between(start, end)]
            if filters.machine_id:
                capacity_conditions.append(Capacity.machine_id == filters.machine_id)
            total = session.scalar(select(func.count(Capacity.id)).where(*capacity_conditions)) or 0
            page, size = _pager(total, "capacity_report")
            _, rows = capacity_page(session, filters, page, size)
            st.dataframe(rows, hide_index=True, use_container_width=True)
            _downloads(rows, "capacidade")
            if total > len(rows):
                _filtered_download(total, lambda p, s: capacity_page(session, filters, p, s),
                                   "capacidade", filters)
