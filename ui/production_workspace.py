"""Production page with the per-order spreadsheet and start/finish controls."""

from ui.operation_grid import render as render_grid
from ui.styles import page_header


def render(factory, user):
    page_header(
        "Produção",
        "Consulte os pedidos e registre o início ou o fim da produção.",
        "Andamento por pedido",
        "Sistema ativo",
        "P",
    )
    render_grid(factory, user)
