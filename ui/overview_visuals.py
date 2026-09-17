"""Glass dashboard cards built from the operational overview data."""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape

import streamlit as st

from ui.operation_grid import _status_label
from utils.formatters import date_br


def _s(value) -> str:
    return escape(str(value or ""))


def _kpi(label: str, value: int, detail: str, tone: str) -> str:
    return (f'<div class="jr-ov-kpi {tone}"><span class="jr-ov-kpi-dot"></span>'
            f'<span class="jr-ov-kpi-label">{_s(label)}</span>'
            f'<strong>{value}</strong><small>{_s(detail)}</small></div>')


def _mini(label: str, value: int, tone: str) -> str:
    return (f'<div class="jr-ov-mini {tone}"><span>{_s(label)}</span>'
            f'<strong>{value}</strong></div>')


def _order_line(order, *, delivery: bool = False) -> str:
    code = _s(order.codigo_interno)
    client = _s(order.cliente_pdf or "Cliente ainda não identificado")
    city = _s(order.cidade or "Cidade pendente")
    side = (f'<span class="jr-ov-date">{_s(date_br(order.previsao_entrega))}</span>'
            if delivery else f'<span class="jr-ov-state">{_s(_status_label(order.status_producao))}</span>')
    return (f'<div class="jr-ov-line"><span class="jr-ov-order-code">{code}</span>'
            f'<span class="jr-ov-line-copy"><b>{client}</b><small>{city}</small></span>{side}</div>')


def _panel(title: str, subtitle: str, body: str, empty: str) -> str:
    content = body or f'<div class="jr-ov-empty">{_s(empty)}</div>'
    return (f'<section class="jr-ov-panel"><div class="jr-ov-panel-head">'
            f'<div><h3>{_s(title)}</h3><p>{_s(subtitle)}</p></div></div>'
            f'<div class="jr-ov-panel-body">{content}'
            '</div></section>')


def _production_line(row: dict, now: datetime) -> str:
    started = row.get("_start_at")
    if started and started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    elapsed = max(0, int((now - started).total_seconds())) if started else 0
    estimated = int(row.get("_estimate_seconds") or 0)
    progress = min(100, round(elapsed * 100 / estimated)) if estimated else 0
    due = bool(estimated and elapsed >= estimated)
    label = "Prazo atingido" if due else f"{progress}% do tempo estimado" if estimated else "Sem estimativa"
    tone = "due" if due else ""
    return (
        f'<div class="jr-ov-live-row {tone}">'
        '<div class="jr-ov-live-top"><div class="jr-ov-live-name">'
        f'<span class="jr-ov-live-pulse"></span><strong>Pedido {_s(row.get("Pedido"))}</strong>'
        f'<span>{_s(row.get("Cliente") or "Cliente pendente")}</span></div>'
        f'<span class="jr-ov-live-tag">{_s(label)}</span></div>'
        f'<div class="jr-ov-live-meta">Início {_s(row.get("Inicio producao"))}'
        f' · Previsão {_s(row.get("Tempo estimado"))}'
        f' · {_s(row.get("Iniciado por") or "Responsável não informado")}</div>'
        f'<div class="jr-ov-live-track" role="progressbar" aria-valuenow="{progress}" '
        f'aria-valuemin="0" aria-valuemax="100"><span style="width:{progress}%"></span></div>'
        '</div>'
    )


def render_visuals(data: dict, production_rows: list[dict], now: datetime) -> None:
    total = max(1, int(data["total"]))
    waiting_pct = round(data["awaiting"] * 100 / total, 2)
    active_pct = round(data["active"] * 100 / total, 2)
    finished_pct = round((data["finished"] + data["delivered"]) * 100 / total, 2)
    kpis = "".join((
        _kpi("Pedidos", data["total"], "No sistema", "blue"),
        _kpi("Em produção", data["active"], "Em andamento agora", "teal"),
        _kpi("Aguardando", data["awaiting"], "A iniciar", "amber"),
        _kpi("Concluídos", data["finished"], "Produção finalizada", "green"),
    ))
    minis = "".join((
        _mini("Entregues", data["delivered"], "green"),
        _mini("Próximas entregas", data["due_soon"], "blue"),
        _mini("Atrasados", data["overdue"], "red"),
        _mini("Falta material", data["shortages"], "amber"),
        _mini("Sem carrinho", data["missing_cart"], "blue"),
    ))
    live_rows = "".join(_production_line(row, now) for row in production_rows[:5])
    deliveries = "".join(_order_line(order, delivery=True) for order in data["upcoming"][:5])
    waiting = "".join(_order_line(order) for order in data["waiting"][:5])
    recent = "".join(_order_line(order) for order in data["recent"][:5])
    attention = "".join(_order_line(order, delivery=True) for order in data["late"][:3])
    material = "".join(_order_line(order) for order in data["material"][:3])
    attention_body = ""
    if attention:
        attention_body += '<div class="jr-ov-subhead">Entregas atrasadas</div>' + attention
    if material:
        attention_body += '<div class="jr-ov-subhead">Falta de material</div>' + material
    html = f"""
    <style>
      .jr-ov * {{ box-sizing: border-box; }}
      .jr-ov {{ color: #193450; }}
      .jr-ov-kpis {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin: 14px 0; }}
      .jr-ov-kpi {{ position: relative; overflow: hidden; display: flex; flex-direction: column;
        min-height: 142px; padding: 20px 22px; border: 1px solid rgba(255,255,255,.95);
        border-radius: 20px; background: linear-gradient(145deg, rgba(255,255,255,.92), rgba(247,251,255,.72));
        box-shadow: 0 15px 38px rgba(43,77,114,.1); backdrop-filter: blur(18px); }}
      .jr-ov-kpi:after {{ content: ""; position: absolute; right: -25px; bottom: -49px;
        width: 120px; height: 120px; border-radius: 50%; background: var(--ov-soft); }}
      .jr-ov-kpi.blue {{ --ov-accent: #3676d0; --ov-soft: rgba(66,134,226,.12); }}
      .jr-ov-kpi.teal {{ --ov-accent: #22a9ab; --ov-soft: rgba(46,197,188,.16); }}
      .jr-ov-kpi.amber {{ --ov-accent: #d59b37; --ov-soft: rgba(239,186,76,.16); }}
      .jr-ov-kpi.green {{ --ov-accent: #2eaf85; --ov-soft: rgba(54,193,148,.15); }}
      .jr-ov-kpi-dot {{ width: 27px; height: 4px; border-radius: 10px; background: var(--ov-accent); margin-bottom: 11px; }}
      .jr-ov-kpi-label {{ font-size: 13px; color: #5e7790; font-weight: 760; }}
      .jr-ov-kpi strong {{ font-size: 34px; line-height: 1.08; margin-top: 8px; letter-spacing: -.04em; }}
      .jr-ov-kpi small {{ font-size: 12px; color: #7a8fa3; margin-top: 7px; }}
      .jr-ov-flow {{ background: rgba(255,255,255,.76); border: 1px solid rgba(255,255,255,.92);
        border-radius: 18px; padding: 16px 19px; box-shadow: 0 14px 35px rgba(40,77,112,.08); }}
      .jr-ov-flow-head {{ display: flex; justify-content: space-between; gap: 10px;
        font-size: 13px; font-weight: 780; margin-bottom: 11px; }}
      .jr-ov-flow-head span:last-child {{ color: #68829b; font-weight: 600; }}
      .jr-ov-flow-bar {{ display: flex; height: 11px; overflow: hidden; border-radius: 30px;
        background: #e5edf5; }}
      .jr-ov-flow-bar span {{ height: 100%; transition: width .7s ease; }}
      .jr-ov-flow-bar .waiting {{ background: #91b4e5; }}
      .jr-ov-flow-bar .active {{ background: linear-gradient(90deg,#3c82ce,#36bec1); }}
      .jr-ov-flow-bar .done {{ background: #54c69e; }}
      .jr-ov-flow-legend {{ display: flex; flex-wrap: wrap; gap: 9px 20px; margin-top: 10px;
        font-size: 11px; color: #67809a; }}
      .jr-ov-flow-legend b {{ color: #294a67; }}
      .jr-ov-minis {{ display: grid; grid-template-columns: repeat(5,minmax(0,1fr)); gap: 10px; margin: 13px 0 22px; }}
      .jr-ov-mini {{ display: flex; justify-content: space-between; align-items: center; gap: 8px;
        padding: 11px 13px; border-radius: 13px; border: 1px solid rgba(255,255,255,.94);
        background: rgba(255,255,255,.66); box-shadow: 0 10px 26px rgba(43,77,114,.07);
        font-size: 11px; color: #68829a; font-weight: 730; }}
      .jr-ov-mini strong {{ font-size: 18px; color: #2e6fb7; }}
      .jr-ov-mini.green strong {{ color: #239a74; }} .jr-ov-mini.amber strong {{ color: #b47722; }}
      .jr-ov-mini.red strong {{ color: #c75863; }}
      .jr-ov-board {{ display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 15px; margin-bottom: 15px; }}
      .jr-ov-panel {{ min-width: 0; border: 1px solid rgba(255,255,255,.95); border-radius: 21px;
        background: rgba(255,255,255,.73); box-shadow: 0 17px 42px rgba(41,78,114,.09);
        backdrop-filter: blur(18px); overflow: hidden; }}
      .jr-ov-panel-head {{ padding: 19px 21px 13px; border-bottom: 1px solid rgba(133,165,192,.16); }}
      .jr-ov-panel h3 {{ font-size: 17px; margin: 0; color: #1c3953; font-weight: 830; }}
      .jr-ov-panel p {{ color: #8093a7; font-size: 12px; margin: 4px 0 0; }}
      .jr-ov-panel-body {{ padding: 6px 12px 12px; }}
      .jr-ov-line {{ display: flex; align-items: center; gap: 12px; min-height: 59px; padding: 9px 7px;
        border-bottom: 1px solid rgba(113,150,183,.13); }}
      .jr-ov-line:last-child {{ border-bottom: none; }}
      .jr-ov-order-code {{ flex: 0 0 auto; border-radius: 9px; background: #eaf3fb; color: #246599;
        padding: 6px 8px; font-size: 11px; font-weight: 850; }}
      .jr-ov-line-copy {{ min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 3px; }}
      .jr-ov-line-copy b {{ white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        font-size: 12px; color: #29455e; }}
      .jr-ov-line-copy small {{ color: #879aaa; font-size: 11px; }}
      .jr-ov-date {{ font-size: 11px; color: #217e9b; font-weight: 760; white-space: nowrap; }}
      .jr-ov-state {{ max-width: 130px; color: #607b96; font-size: 10px; text-align: right; font-weight: 760; }}
      .jr-ov-empty {{ padding: 28px 13px; text-align: center; color: #879bad; font-size: 12px; }}
      .jr-ov-subhead {{ font-size: 11px; color: #ab7043; text-transform: uppercase;
        font-weight: 820; letter-spacing: .1em; padding: 12px 7px 4px; }}
      .jr-ov-live-panel {{ border-radius: 22px; margin-bottom: 15px; padding: 20px 22px;
        background: linear-gradient(125deg,#173657 0%,#235e7d 66%,#2e8c92 100%);
        border: 1px solid rgba(255,255,255,.55); color: white;
        box-shadow: 0 20px 50px rgba(24,76,108,.2); }}
      .jr-ov-live-header {{ display: flex; justify-content: space-between; align-items: center; gap: 10px;
        margin-bottom: 12px; }}
      .jr-ov-live-header h3 {{ color: white; margin: 0; font-size: 19px; }}
      .jr-ov-live-header small {{ color: #cce7ed; font-size: 11px; }}
      .jr-ov-live-row {{ padding: 14px 2px; border-top: 1px solid rgba(255,255,255,.16); }}
      .jr-ov-live-top {{ display: flex; align-items: center; justify-content: space-between; gap: 10px; }}
      .jr-ov-live-name {{ display: flex; align-items: center; gap: 8px; min-width: 0; }}
      .jr-ov-live-name strong {{ white-space: nowrap; font-size: 13px; }}
      .jr-ov-live-name span:last-child {{ font-size: 12px; color: #d3e8f0; overflow: hidden;
        text-overflow: ellipsis; white-space: nowrap; }}
      .jr-ov-live-pulse {{ flex: 0 0 7px; width: 7px; height: 7px; border-radius: 50%;
        background: #67f0bb; box-shadow: 0 0 0 4px rgba(103,240,187,.16); animation: jr-ov-pulse 1.8s infinite; }}
      .jr-ov-live-tag {{ border-radius: 999px; padding: 5px 8px; white-space: nowrap; font-size: 10px;
        background: rgba(255,255,255,.15); color: #e5f8fa; }}
      .jr-ov-live-row.due .jr-ov-live-tag {{ color: #ffe5b9; background: rgba(255,209,125,.17); }}
      .jr-ov-live-meta {{ margin: 9px 0; font-size: 11px; color: #c9e2eb; }}
      .jr-ov-live-track {{ height: 7px; border-radius: 20px; overflow: hidden;
        background: rgba(255,255,255,.19); }}
      .jr-ov-live-track span {{ display: block; height: 100%; border-radius: 20px;
        background: linear-gradient(90deg,#69dcae,#acf2dd); animation: jr-ov-grow .9s ease both; }}
      .jr-ov-live-row.due .jr-ov-live-track span {{ background: linear-gradient(90deg,#eec36f,#ffdb98); }}
      .jr-ov-live-empty {{ color: #d9eaf0; font-size: 13px; padding: 24px 3px 14px; }}
      @keyframes jr-ov-pulse {{ 50% {{ box-shadow: 0 0 0 10px rgba(103,240,187,0); }} }}
      @keyframes jr-ov-grow {{ from {{ transform: scaleX(0); transform-origin: left; }} to {{ transform: scaleX(1); transform-origin: left; }} }}
      @media(max-width:900px) {{ .jr-ov-kpis {{ grid-template-columns: repeat(2,minmax(0,1fr)); }}
        .jr-ov-minis {{ grid-template-columns: repeat(3,minmax(0,1fr)); }} }}
      @media(max-width:650px) {{ .jr-ov-board {{ grid-template-columns: 1fr; }}
        .jr-ov-minis {{ grid-template-columns: repeat(2,minmax(0,1fr)); }}
        .jr-ov-live-name span:last-child {{ display: none; }} }}
      @media(prefers-reduced-motion:reduce) {{ .jr-ov-live-pulse,.jr-ov-live-track span {{ animation: none; }} }}
    </style>
    <div class="jr-ov">
      <div class="jr-ov-kpis">{kpis}</div>
      <div class="jr-ov-flow"><div class="jr-ov-flow-head"><span>Fluxo dos pedidos</span>
        <span>{data['total']} pedido(s)</span></div>
        <div class="jr-ov-flow-bar" role="img" aria-label="Distribuição dos pedidos por andamento">
          <span class="waiting" style="width:{waiting_pct}%"></span>
          <span class="active" style="width:{active_pct}%"></span>
          <span class="done" style="width:{finished_pct}%"></span>
        </div><div class="jr-ov-flow-legend"><span>● Aguardando <b>{data['awaiting']}</b></span>
          <span>● Em produção <b>{data['active']}</b></span>
          <span>● Concluídos e entregues <b>{data['finished'] + data['delivered']}</b></span></div></div>
      <div class="jr-ov-minis">{minis}</div>
      <section class="jr-ov-live-panel"><div class="jr-ov-live-header"><h3>Produção agora</h3>
        <small>{data['active']} pedido(s) em andamento</small></div>
        {live_rows or '<div class="jr-ov-live-empty">Nenhum pedido em produção no momento.</div>'}
      </section>
      <div class="jr-ov-board">
        {_panel('Próximas entregas', 'Datas confirmadas manualmente', deliveries, 'Nenhuma entrega futura definida.')}
        {_panel('Aguardando produção', 'Pedidos para iniciar', waiting, 'Nenhum pedido aguardando início.')}
        {_panel('Pedidos recentes', 'Últimos registros importados', recent, 'Nenhum pedido importado ainda.')}
        {_panel('Precisa de atenção', 'Atrasos e falta de material', attention_body, 'Nenhuma pendência urgente agora.')}
      </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)
