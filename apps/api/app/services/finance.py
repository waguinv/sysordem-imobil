"""Cálculos financeiros em Decimal (nunca float).

As fórmulas são as usuais de mercado, mas juros, correção e multa precisam ser validados por
profissional habilitado para cada empresa/contrato (ver docs/DECISOES_PENDENTES.md).
"""

from decimal import ROUND_HALF_UP, Decimal, localcontext

CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    """Arredonda para centavos, meio para cima (padrão comercial)."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def installment_amount(principal: Decimal, monthly_rate_pct: Decimal, n: int) -> Decimal:
    """Valor da parcela fixa.

    - n = 0: venda à vista (sem parcelas) → 0.
    - taxa 0: divisão simples do saldo.
    - taxa > 0: Tabela Price — PMT = P·i / (1 − (1 + i)^−n).

    O ajuste de centavos da última parcela é feito ao gerar o cronograma (Fase 3).
    """
    if n < 0:
        raise ValueError("número de parcelas negativo")
    if principal < 0:
        raise ValueError("saldo negativo")
    if n == 0:
        if principal != 0:
            raise ValueError("venda sem parcelas exige saldo zero")
        return Decimal("0.00")
    if principal == 0:
        return Decimal("0.00")
    with localcontext() as ctx:
        ctx.prec = 34
        i = monthly_rate_pct / Decimal(100)
        if i == 0:
            return money(principal / n)
        factor = (Decimal(1) + i) ** (-n)
        return money(principal * i / (Decimal(1) - factor))
