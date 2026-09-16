"""Order totals and concise completeness checks."""

from decimal import Decimal

from db.models import Order


MEASURES = ("chapas", "cortes", "metros_lineares_corte", "pecas", "fita_aplicada", "usinagens")


def order_totals(order: Order) -> dict[str, Decimal]:
    return {name: sum((getattr(service, name) or Decimal(0) for service in order.services), Decimal(0))
            for name in MEASURES}


def completeness(order: Order) -> dict[str, bool]:
    return {
        "Serviço importado": bool(order.services),
        "Carrinho importado": bool(order.carrinho),
        "Cliente identificado": bool(order.cliente_pdf),
        "Endereço identificado": bool(order.endereco),
        "Cidade identificada": bool(order.cidade),
        "Produtos identificados": bool(order.items),
        "Produção programada": bool(order.programado_em),
    }
