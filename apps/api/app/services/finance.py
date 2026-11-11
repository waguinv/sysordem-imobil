"""Cálculos financeiros em Decimal (nunca float).

As fórmulas são as usuais de mercado, mas juros, correção e multa precisam ser validados por
profissional habilitado para cada empresa/contrato (ver docs/DECISOES_PENDENTES.md).
"""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
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


def first_installment_sac(principal: Decimal, monthly_rate_pct: Decimal, n: int) -> Decimal:
    """SAC: amortização constante (P/n) + juros sobre o saldo; a 1ª parcela é a maior e as demais caem.

    1ª parcela = P/n + P·i. O cronograma completo (parcelas decrescentes) é gerado na Fase 3.
    """
    if n <= 0:
        raise ValueError("SAC exige ao menos uma parcela")
    if principal < 0:
        raise ValueError("saldo negativo")
    with localcontext() as ctx:
        ctx.prec = 34
        i = monthly_rate_pct / Decimal(100)
        return money(principal / n + principal * i)


def first_installment(principal: Decimal, monthly_rate_pct: Decimal, n: int, amortization: str) -> Decimal:
    """Valor da 1ª parcela no sistema escolhido (Price: todas iguais; SAC: decrescentes)."""
    if amortization == "sac" and n > 0:
        return first_installment_sac(principal, monthly_rate_pct, n)
    return installment_amount(principal, monthly_rate_pct, n)


# ───────────────────────── Fase 3a: cronograma e encargos ─────────────────────────


@dataclass(frozen=True)
class ScheduleRow:
    number: int  # 0 = entrada
    due_date: date
    principal: Decimal  # amortização
    interest: Decimal  # juros do financiamento embutidos na parcela
    amount: Decimal  # principal + interest


def add_months(d: date, months: int) -> date:
    """Mesmo dia nos meses seguintes; em meses mais curtos, o último dia (31/01 → 28/02)."""
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, monthrange(year, month)[1]))


def build_schedule(
    *,
    total: Decimal,
    down_payment: Decimal,
    n: int,
    monthly_rate_pct: Decimal,
    amortization: str,
    first_due: date | None,
    down_payment_due: date,
) -> list[ScheduleRow]:
    """Cronograma completo: entrada (nº 0) + n parcelas mensais.

    - Price: parcela fixa; juros sobre o saldo devedor; a amortização é a diferença.
    - SAC: amortização constante P/n; juros sobre o saldo (parcelas decrescentes).
    - Centavos: a ÚLTIMA parcela absorve o arredondamento, de modo que a soma das amortizações
      é exatamente o saldo financiado (total − entrada).
    """
    rows: list[ScheduleRow] = []
    if down_payment > 0:
        dp = money(down_payment)
        rows.append(ScheduleRow(0, down_payment_due, dp, Decimal("0.00"), dp))
    principal = money(total - down_payment)
    if n == 0:
        if principal != 0:
            raise ValueError("venda sem parcelas exige saldo zero")
        return rows
    if first_due is None:
        raise ValueError("informe o 1º vencimento")
    with localcontext() as ctx:
        ctx.prec = 34
        i = monthly_rate_pct / Decimal(100)
        pmt = installment_amount(principal, monthly_rate_pct, n) if amortization != "sac" else None
        amort_sac = money(principal / n)
        balance = principal
        for k in range(1, n + 1):
            interest = money(balance * i)
            if k == n:
                amort = balance  # ajuste de centavos
            elif amortization == "sac":
                amort = amort_sac
            else:
                assert pmt is not None
                amort = pmt - interest
            balance -= amort
            rows.append(ScheduleRow(k, add_months(first_due, k - 1), amort, interest, amort + interest))
    return rows


@dataclass(frozen=True)
class Settlement:
    """Divisão de um recebimento: parte da parcela + multa + juros de mora."""

    principal: Decimal
    late_fee: Decimal
    late_interest: Decimal
    days_late: int

    @property
    def total(self) -> Decimal:
        return self.principal + self.late_fee + self.late_interest


def late_factor(
    days_late: int, late_fee_pct: Decimal, late_interest_monthly_pct: Decimal
) -> tuple[Decimal, Decimal]:
    """(multa, juros de mora) como fração do valor pago em atraso. Mora pro rata die (mês de 30 dias)."""
    if days_late <= 0:
        return Decimal(0), Decimal(0)
    return late_fee_pct / 100, late_interest_monthly_pct / 100 * days_late / 30


def quote(
    remaining: Decimal,
    *,
    due_date: date,
    on: date,
    late_fee_pct: Decimal,
    late_interest_monthly_pct: Decimal,
) -> Settlement:
    """Quanto custa quitar o restante da parcela na data `on`."""
    days = max(0, (on - due_date).days)
    with localcontext() as ctx:
        ctx.prec = 34
        f_fee, f_int = late_factor(days, late_fee_pct, late_interest_monthly_pct)
        return Settlement(remaining, money(remaining * f_fee), money(remaining * f_int), days)


def split_payment(
    amount: Decimal,
    remaining: Decimal,
    *,
    due_date: date,
    on: date,
    late_fee_pct: Decimal,
    late_interest_monthly_pct: Decimal,
) -> Settlement:
    """Divide o valor recebido entre parcela e encargos.

    Encargos incidem só sobre a parte da parcela quitada agora (pagamento parcial em atraso não paga
    multa duas vezes sobre o mesmo valor). Valor acima do devido → ValueError.
    """
    full = quote(
        remaining,
        due_date=due_date,
        on=on,
        late_fee_pct=late_fee_pct,
        late_interest_monthly_pct=late_interest_monthly_pct,
    )
    if amount > full.total:
        raise ValueError(f"Valor acima do devido nesta data ({full.total})")
    if amount == full.total:
        return full
    with localcontext() as ctx:
        ctx.prec = 34
        f_fee, f_int = late_factor(full.days_late, late_fee_pct, late_interest_monthly_pct)
        principal = min(remaining, money(amount / (1 + f_fee + f_int)))
        fee = money(principal * f_fee)
        interest = amount - principal - fee
        if interest < 0:  # arredondamento em valores muito pequenos
            fee, interest = fee + interest, Decimal("0.00")
        return Settlement(principal, fee, interest, full.days_late)
