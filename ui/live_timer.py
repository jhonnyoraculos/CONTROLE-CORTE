"""Browser-side live production clock without rerunning the Streamlit page."""

from __future__ import annotations

from html import escape

import streamlit as st

from services.producao_service import ProductionClock


def render_timer(clock: ProductionClock, order_code: str) -> None:
    start_ms = int(clock.start_at.timestamp() * 1000)
    estimated = int(clock.estimated_seconds)
    html = f"""
    <!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
    <style>
      * {{ box-sizing: border-box; }}
      body {{ margin: 0; font-family: system-ui, -apple-system, sans-serif; color: #eaf6ff; }}
      .panel {{ position: relative; overflow: hidden; border-radius: 22px;
        background: linear-gradient(125deg, #163253 0%, #225b7b 58%, #277f92 100%);
        border: 1px solid rgba(255,255,255,.3); padding: 23px 25px 19px;
        box-shadow: 0 18px 42px rgba(19,59,91,.22); }}
      .panel:after {{ content: ""; position: absolute; width: 220px; height: 220px;
        right: -70px; top: -115px; border-radius: 50%; background: rgba(123,231,225,.13); }}
      .head {{ position: relative; display: flex; justify-content: space-between; align-items: center;
        gap: 12px; z-index: 1; }}
      .eyebrow {{ font-size: 11px; font-weight: 800; letter-spacing: .15em; text-transform: uppercase;
        color: #bfe4ef; }}
      .order {{ font-size: 15px; font-weight: 750; margin-top: 4px; }}
      .live {{ display: inline-flex; align-items: center; gap: 9px; padding: 8px 12px;
        border-radius: 999px; background: rgba(255,255,255,.14); font-size: 12px; font-weight: 800;
        white-space: nowrap; }}
      .dot {{ width: 8px; height: 8px; border-radius: 50%; background: #6ef4b8;
        box-shadow: 0 0 0 5px rgba(110,244,184,.18); animation: pulse 1.8s infinite; }}
      .times {{ position: relative; display: grid; grid-template-columns: 1.3fr 1fr 1fr; gap: 13px;
        margin: 20px 0 17px; z-index: 1; }}
      .time {{ border: 1px solid rgba(255,255,255,.18); border-radius: 15px;
        background: rgba(255,255,255,.1); padding: 13px 14px; }}
      .label {{ color: #c8e2ef; font-size: 11px; font-weight: 700; margin-bottom: 7px; }}
      .value {{ font-variant-numeric: tabular-nums; font-size: 20px; font-weight: 850;
        letter-spacing: .02em; white-space: nowrap; }}
      .time:first-child .value {{ font-size: 26px; }}
      .track {{ position: relative; height: 10px; border-radius: 20px; overflow: hidden;
        background: rgba(255,255,255,.18); z-index: 1; }}
      .fill {{ height: 100%; width: 0; border-radius: 20px;
        background: linear-gradient(90deg, #58dfa7, #a6f5da); transition: width .9s linear; }}
      .foot {{ position: relative; display: flex; justify-content: space-between; gap: 10px;
        font-size: 11px; color: #d0e8f1; margin-top: 9px; z-index: 1; }}
      .due {{ display: none; margin-top: 14px; border-radius: 12px; padding: 11px 13px;
        background: rgba(255,215,153,.19); border: 1px solid rgba(255,215,153,.4);
        color: #fff3dd; font-size: 13px; font-weight: 750; }}
      .due.visible {{ display: block; }}
      @keyframes pulse {{ 50% {{ box-shadow: 0 0 0 10px rgba(110,244,184,0); }} }}
      @media (max-width: 640px) {{ .times {{ grid-template-columns: 1fr 1fr; }}
        .time:first-child {{ grid-column: 1 / -1; }} .value {{ font-size: 18px; }} }}
      @media (prefers-reduced-motion: reduce) {{ .dot {{ animation: none; }} }}
    </style></head><body>
      <div class="panel">
        <div class="head"><div><div class="eyebrow">Produção em andamento</div>
          <div class="order">Pedido {escape(str(order_code))}</div></div>
          <div class="live"><span class="dot"></span>AO VIVO</div></div>
        <div class="times">
          <div class="time"><div class="label">Tempo decorrido</div><div class="value" id="elapsed">00:00:00</div></div>
          <div class="time"><div class="label">Estimativa</div><div class="value" id="estimate">—</div></div>
          <div class="time"><div class="label">Tempo restante</div><div class="value" id="remaining">—</div></div>
        </div>
        <div class="track"><div class="fill" id="fill"></div></div>
        <div class="foot"><span id="progress-label">Acompanhando o pedido</span>
          <span id="percent"></span></div>
        <div class="due" id="due">O prazo estimado terminou. A produção acabou? Confirme abaixo.</div>
      </div>
      <script>
        const startMs = {start_ms};
        const estimated = {estimated};
        function fmt(value) {{
          const n = Math.max(0, Math.floor(value));
          const h = Math.floor(n / 3600);
          const m = Math.floor((n % 3600) / 60);
          const s = n % 60;
          return String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
        }}
        function tick() {{
          const elapsed = Math.max(0, Math.floor((Date.now() - startMs) / 1000));
          document.getElementById('elapsed').textContent = fmt(elapsed);
          if (estimated > 0) {{
            const remaining = Math.max(0, estimated - elapsed);
            const pct = Math.min(100, Math.floor(elapsed * 100 / estimated));
            document.getElementById('estimate').textContent = fmt(estimated);
            document.getElementById('remaining').textContent = fmt(remaining);
            document.getElementById('fill').style.width = pct + '%';
            document.getElementById('percent').textContent = pct + '%';
            document.getElementById('progress-label').textContent = remaining ? 'Prazo estimado' : 'Prazo estimado atingido';
            document.getElementById('due').classList.toggle('visible', remaining === 0);
          }} else {{
            document.getElementById('estimate').textContent = 'Sem medidas';
            document.getElementById('remaining').textContent = '—';
            document.getElementById('progress-label').textContent = 'Contagem desde o início';
          }}
        }}
        tick(); setInterval(tick, 1000);
      </script>
    </body></html>
    """
    if hasattr(st, "iframe"):
        st.iframe(html, height=370, width="stretch")
    else:
        st.components.v1.html(html, height=370, scrolling=False)
