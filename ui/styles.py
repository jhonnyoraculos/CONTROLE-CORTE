"""Shared visual language for the Streamlit interface."""

from __future__ import annotations

from html import escape

import streamlit as st


def inject_styles():
    st.markdown(
        """
    <style>
    :root {
      --jr-ink: #10243d;
      --jr-muted: #6d8099;
      --jr-line: rgba(132, 156, 181, .26);
      --jr-panel: rgba(255, 255, 255, .72);
      --jr-panel-strong: rgba(255, 255, 255, .88);
      --jr-blue: #2f6fd7;
      --jr-cyan: #35a3c7;
      --jr-green: #45c99a;
      --jr-warn: #e59f37;
      --jr-danger: #df5b66;
      --jr-sidebar: #173a5c;
    }

    html, body, [data-testid="stAppViewContainer"] {
      background:
        radial-gradient(circle at 84% 9%, rgba(106, 152, 220, .24), transparent 31rem),
        radial-gradient(circle at 76% 82%, rgba(79, 202, 177, .18), transparent 30rem),
        linear-gradient(135deg, #eef6fb 0%, #f8fbff 45%, #edf4ff 100%);
      color: var(--jr-ink);
    }

    .block-container {
      max-width: 1420px;
      padding-top: 1.55rem;
      padding-bottom: 3rem;
    }

    h1, h2, h3 {
      color: var(--jr-ink);
      letter-spacing: 0;
      font-weight: 800;
    }

    p, label, span, div {
      letter-spacing: 0;
    }

    [data-testid="stSidebar"] {
      background:
        linear-gradient(180deg, rgba(16, 45, 75, .98) 0%, rgba(32, 76, 118, .98) 100%);
      border-right: 1px solid rgba(255, 255, 255, .14);
    }

    [data-testid="stSidebar"] * {
      color: rgba(255, 255, 255, .86);
    }

    [data-testid="stSidebar"] a,
    [data-testid="stSidebar"] button {
      border-radius: 12px;
      color: rgba(255, 255, 255, .9) !important;
    }

    [data-testid="stSidebar"] [aria-selected="true"],
    [data-testid="stSidebar"] a:hover {
      background: rgba(255, 255, 255, .16);
      box-shadow: inset 0 0 0 1px rgba(255, 255, 255, .14);
    }

    [data-testid="stSidebar"] hr {
      border-color: rgba(255, 255, 255, .18);
    }

    .jr-hero {
      position: relative;
      overflow: hidden;
      border: 1px solid rgba(255, 255, 255, .78);
      border-radius: 0 0 22px 22px;
      padding: 1.8rem 2rem;
      margin-bottom: 1.05rem;
      background:
        linear-gradient(135deg, rgba(255, 255, 255, .84), rgba(244, 249, 255, .58)),
        radial-gradient(circle at 92% 18%, rgba(88, 151, 225, .18), transparent 22rem);
      box-shadow: 0 22px 70px rgba(56, 82, 123, .14);
      backdrop-filter: blur(18px) saturate(1.18);
    }

    .jr-hero::after {
      content: "";
      position: absolute;
      right: -4rem;
      bottom: -5rem;
      width: 18rem;
      height: 18rem;
      border-radius: 50%;
      background: rgba(75, 202, 185, .15);
      pointer-events: none;
    }

    .jr-eyebrow {
      color: #49739b;
      font-size: .72rem;
      font-weight: 800;
      letter-spacing: .18em;
      text-transform: uppercase;
      margin-bottom: .85rem;
    }

    .jr-hero-main {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
    }

    .jr-hero-title {
      display: flex;
      align-items: center;
      gap: .9rem;
    }

    .jr-icon {
      width: 3rem;
      height: 3rem;
      display: grid;
      place-items: center;
      border-radius: 13px;
      color: #fff;
      font-weight: 800;
      background: linear-gradient(135deg, var(--jr-blue), var(--jr-cyan));
      box-shadow: 0 12px 34px rgba(51, 126, 200, .25);
    }

    .jr-hero h1 {
      font-size: clamp(2rem, 3vw, 2.65rem);
      line-height: 1.05;
      margin: 0;
    }

    .jr-subtitle {
      color: var(--jr-muted);
      margin: .9rem 0 0 4rem;
      font-size: 1rem;
    }

    .jr-badge {
      display: inline-flex;
      align-items: center;
      gap: .45rem;
      border: 1px solid rgba(255, 255, 255, .75);
      background: rgba(255, 255, 255, .7);
      border-radius: 999px;
      padding: .55rem .8rem;
      color: #486078;
      font-weight: 700;
      white-space: nowrap;
    }

    .jr-dot {
      width: .55rem;
      height: .55rem;
      border-radius: 999px;
      background: var(--jr-green);
      box-shadow: 0 0 0 4px rgba(69, 201, 154, .18);
    }

    .jr-glass {
      border: 1px solid rgba(255, 255, 255, .78);
      border-radius: 18px;
      background: var(--jr-panel);
      box-shadow: 0 18px 52px rgba(55, 84, 122, .11);
      backdrop-filter: blur(16px) saturate(1.14);
      padding: 1rem;
      margin: .7rem 0 1rem;
    }

    .jr-card-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: .85rem;
      margin: .85rem 0 1rem;
    }

    .jr-card {
      position: relative;
      overflow: hidden;
      min-height: 5.2rem;
      border: 1px solid rgba(255, 255, 255, .76);
      border-radius: 16px;
      padding: .9rem 1rem;
      background: rgba(255, 255, 255, .76);
      box-shadow: 0 14px 38px rgba(46, 75, 114, .10);
    }

    .jr-card::before {
      content: "";
      position: absolute;
      left: 1rem;
      right: 1rem;
      top: 0;
      height: 3px;
      border-radius: 999px;
      background: linear-gradient(90deg, var(--jr-blue), var(--jr-cyan));
    }

    .jr-card.good::before { background: linear-gradient(90deg, var(--jr-green), var(--jr-cyan)); }
    .jr-card.warn::before { background: linear-gradient(90deg, var(--jr-warn), #f3c36b); }
    .jr-card.bad::before { background: linear-gradient(90deg, var(--jr-danger), #f08b94); }
    .jr-card.neutral::before { background: linear-gradient(90deg, #7e97b1, #a4b8ca); }

    .jr-card-label {
      color: #607895;
      font-size: .72rem;
      font-weight: 800;
      text-transform: uppercase;
      margin-bottom: .65rem;
    }

    .jr-card-value {
      color: var(--jr-ink);
      font-size: 1.45rem;
      font-weight: 850;
      line-height: 1;
    }

    .jr-card-note {
      color: var(--jr-muted);
      font-size: .82rem;
      margin-top: .45rem;
    }

    .jr-chip-row {
      display: flex;
      flex-wrap: wrap;
      gap: .55rem;
      margin: .6rem 0 .9rem;
    }

    .jr-chip {
      display: inline-flex;
      align-items: center;
      border: 1px solid rgba(109, 128, 153, .18);
      border-radius: 999px;
      background: rgba(255, 255, 255, .68);
      color: #4f6680;
      padding: .42rem .7rem;
      font-weight: 700;
      font-size: .82rem;
    }

    .jr-chip.good { color: #126b52; background: rgba(69, 201, 154, .14); }
    .jr-chip.warn { color: #8c5b0d; background: rgba(229, 159, 55, .15); }
    .jr-chip.bad { color: #99404a; background: rgba(223, 91, 102, .14); }

    .stTabs [data-baseweb="tab-list"] {
      gap: .45rem;
      border-bottom: 0;
      background: rgba(255, 255, 255, .42);
      border: 1px solid rgba(255, 255, 255, .7);
      border-radius: 16px;
      padding: .35rem;
    }

    .stTabs [data-baseweb="tab"] {
      border-radius: 12px;
      color: #59718d;
      font-weight: 750;
      padding: .65rem 1rem;
    }

    .stTabs [aria-selected="true"] {
      color: var(--jr-ink);
      background: rgba(255, 255, 255, .9);
      box-shadow: 0 8px 22px rgba(45, 80, 125, .10);
    }

    [data-testid="stMetric"] {
      border: 1px solid rgba(255, 255, 255, .72);
      border-radius: 16px;
      padding: .9rem 1rem;
      background: rgba(255, 255, 255, .72);
      box-shadow: 0 14px 34px rgba(56, 82, 123, .10);
      backdrop-filter: blur(14px);
    }

    [data-testid="stFileUploader"] section {
      border: 1px dashed rgba(61, 103, 144, .28);
      border-radius: 16px;
      background: rgba(255, 255, 255, .58);
      min-height: 4.8rem;
    }

    [data-testid="stDataFrame"] {
      border-radius: 14px;
      overflow: hidden;
      border: 1px solid rgba(109, 128, 153, .17);
      box-shadow: 0 12px 28px rgba(64, 94, 132, .08);
    }

    div[data-testid="stAlert"] {
      border-radius: 14px;
      border: 1px solid rgba(255, 255, 255, .72);
    }

    .stButton > button,
    .stDownloadButton > button,
    button[kind="primary"] {
      border-radius: 12px !important;
      min-height: 2.55rem;
      font-weight: 800 !important;
      border: 1px solid rgba(255, 255, 255, .7) !important;
      box-shadow: 0 12px 28px rgba(47, 111, 215, .18);
    }

    .stButton > button[kind="primary"],
    button[kind="primary"] {
      background: linear-gradient(135deg, var(--jr-blue), var(--jr-cyan)) !important;
      color: #fff !important;
    }

    .stTextInput input,
    .stNumberInput input,
    .stDateInput input,
    .stSelectbox [data-baseweb="select"],
    .stTextArea textarea {
      border-radius: 12px !important;
      border-color: rgba(112, 136, 162, .24) !important;
      background: rgba(255, 255, 255, .74) !important;
    }

    details {
      border-radius: 14px !important;
      background: rgba(255, 255, 255, .58) !important;
      border: 1px solid rgba(255, 255, 255, .68) !important;
    }

    @media (max-width: 760px) {
      .jr-hero { padding: 1.2rem; }
      .jr-hero-main { align-items: flex-start; flex-direction: column; }
      .jr-subtitle { margin-left: 0; }
    }
    </style>
    """,
        unsafe_allow_html=True,
    )


def page_header(title: str, subtitle: str = "", eyebrow: str = "", badge: str = "Sistema ativo",
                icon: str = "") -> None:
    initials = escape(icon or title[:1].upper())
    badge_html = ""
    if badge:
        badge_html = (
            f'<div class="jr-badge"><span class="jr-dot"></span>{escape(badge)}</div>'
        )
    subtitle_html = f'<div class="jr-subtitle">{escape(subtitle)}</div>' if subtitle else ""
    eyebrow_html = f'<div class="jr-eyebrow">{escape(eyebrow)}</div>' if eyebrow else ""
    st.markdown(
        f"""
        <div class="jr-hero">
          {eyebrow_html}
          <div class="jr-hero-main">
            <div>
              <div class="jr-hero-title">
                <div class="jr-icon">{initials}</div>
                <h1>{escape(title)}</h1>
              </div>
              {subtitle_html}
            </div>
            {badge_html}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def metric_cards(cards: list[tuple[str, object, str, str]]) -> None:
    for offset in range(0, len(cards), 4):
        chunk = cards[offset:offset + 4]
        for column, (label, value, note, _tone) in zip(st.columns(len(chunk)), chunk):
            column.metric(str(label), str(value), help=str(note))
            if note:
                column.caption(str(note))


def chips(items: list[tuple[str, str]]) -> None:
    if items:
        st.caption("  |  ".join(label for label, _tone in items))
