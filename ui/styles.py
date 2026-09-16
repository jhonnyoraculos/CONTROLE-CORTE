import streamlit as st


def inject_styles():
    st.markdown("""
    <style>
    .block-container {max-width: 1480px; padding-top: 1.5rem;}
    h1, h2, h3 {letter-spacing: -.025em;}
    [data-testid="stMetric"] {border: 1px solid #dfe5e8; border-radius: 10px;
      padding: .8rem 1rem; background: var(--secondary-background-color);}
    div[data-testid="stAlert"] {border-radius: 8px;}
    </style>
    """, unsafe_allow_html=True)
