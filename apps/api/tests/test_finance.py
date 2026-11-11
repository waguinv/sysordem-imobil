from decimal import Decimal

import pytest

from app.services.finance import installment_amount, money

D = Decimal


def test_money_rounds_half_up_to_cents():
    assert money(D("10.005")) == D("10.01")
    assert money(D("10.004")) == D("10.00")
    assert money(D("0.1") + D("0.2")) == D("0.30")


def test_cash_sale_and_zero_rate():
    assert installment_amount(D("0"), D("0"), 0) == D("0.00")
    assert installment_amount(D("120000.00"), D("0"), 120) == D("1000.00")
    assert installment_amount(D("100.00"), D("0"), 3) == D("33.33")  # ajuste da última parcela na Fase 3


def test_price_table_known_values():
    # 100.000 a 1% a.m. em 12x → 8.884,88 (valor de referência da Tabela Price)
    assert installment_amount(D("100000.00"), D("1"), 12) == D("8884.88")
    # 150.000 a 0,8% a.m. em 120x
    assert installment_amount(D("150000.00"), D("0.8"), 120) == D("1949.19")


def test_price_table_sum_covers_principal():
    pmt = installment_amount(D("50000.00"), D("1.5"), 60)
    assert pmt * 60 > D("50000.00")


def test_invalid_inputs():
    with pytest.raises(ValueError):
        installment_amount(D("10"), D("0"), 0)
    with pytest.raises(ValueError):
        installment_amount(D("-1"), D("0"), 1)
    with pytest.raises(ValueError):
        installment_amount(D("10"), D("0"), -1)


def test_sac_first_installment():
    from app.services.finance import first_installment, first_installment_sac

    # 120.000 em 120x a 1% a.m.: amortização 1.000 + juros 1.200 = 2.200 na 1ª parcela
    assert first_installment_sac(D("120000.00"), D("1"), 120) == D("2200.00")
    assert first_installment(D("120000.00"), D("0"), 120, "sac") == D("1000.00")
    assert first_installment(D("100000.00"), D("1"), 12, "price") == D("8884.88")
    assert first_installment(D("0"), D("0"), 0, "sac") == D("0.00")  # à vista
    with pytest.raises(ValueError):
        first_installment_sac(D("10"), D("1"), 0)


# ───────────────────────── Fase 3a: cronograma e encargos ─────────────────────────

from datetime import date  # noqa: E402

from app.services.finance import add_months, build_schedule, quote, split_payment  # noqa: E402


def test_add_months_clamps_to_month_end():
    assert add_months(date(2027, 1, 31), 1) == date(2027, 2, 28)
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
    assert add_months(date(2027, 11, 15), 2) == date(2028, 1, 15)
    assert add_months(date(2027, 1, 31), 2) == date(2027, 3, 31)  # não "gruda" no dia 28


def _sched(**kw):
    base = {
        "total": D("120000.00"),
        "down_payment": D("20000.00"),
        "n": 12,
        "monthly_rate_pct": D("1"),
        "amortization": "price",
        "first_due": date(2027, 1, 10),
        "down_payment_due": date(2026, 12, 1),
    }
    return build_schedule(**{**base, **kw})


def test_price_schedule_principal_sums_exactly():
    rows = _sched()
    assert rows[0].number == 0 and rows[0].amount == D("20000.00") and rows[0].interest == 0
    parcels = rows[1:]
    assert [r.number for r in parcels] == list(range(1, 13))
    assert sum(r.principal for r in parcels) == D("100000.00")
    assert {r.amount for r in parcels[:-1]} == {D("8884.88")}  # Price: iguais
    assert abs(parcels[-1].amount - D("8884.88")) <= D("0.05")  # última absorve centavos
    assert parcels[0].interest == D("1000.00") and parcels[-1].due_date == date(2027, 12, 10)


def test_sac_schedule_is_decreasing():
    rows = _sched(amortization="sac")[1:]
    assert rows[0].amount == D("9333.33")  # 8.333,33 + 1.000,00
    assert all(a.amount > b.amount for a, b in zip(rows, rows[1:], strict=False))
    assert sum(r.principal for r in rows) == D("100000.00")


def test_zero_rate_and_cash_sale():
    rows = _sched(total=D("100.00"), down_payment=D("0"), n=3, monthly_rate_pct=D("0"))
    assert [r.amount for r in rows] == [D("33.33"), D("33.33"), D("33.34")]
    cash = _sched(total=D("50.00"), down_payment=D("50.00"), n=0, first_due=None)
    assert [(r.number, r.amount) for r in cash] == [(0, D("50.00"))]


def test_quote_on_time_and_late():
    on_time = quote(
        D("1000.00"),
        due_date=date(2027, 1, 10),
        on=date(2027, 1, 10),
        late_fee_pct=D("2"),
        late_interest_monthly_pct=D("1"),
    )
    assert on_time.total == D("1000.00") and on_time.days_late == 0
    late = quote(
        D("1000.00"),
        due_date=date(2027, 1, 10),
        on=date(2027, 2, 9),
        late_fee_pct=D("2"),
        late_interest_monthly_pct=D("1"),
    )
    assert (late.late_fee, late.late_interest, late.days_late) == (D("20.00"), D("10.00"), 30)
    assert late.total == D("1030.00")


def test_split_partial_late_payment_charges_only_paid_part():
    kw = {
        "due_date": date(2027, 1, 10),
        "on": date(2027, 2, 9),
        "late_fee_pct": D("2"),
        "late_interest_monthly_pct": D("1"),
    }
    s = split_payment(D("515.00"), D("1000.00"), **kw)  # metade do devido
    assert (s.principal, s.late_fee, s.late_interest) == (D("500.00"), D("10.00"), D("5.00"))
    rest = split_payment(D("515.00"), D("500.00"), **kw)
    assert rest.principal == D("500.00") and rest.total == D("515.00")
    with pytest.raises(ValueError, match="acima do devido"):
        split_payment(D("1030.01"), D("1000.00"), **kw)
    assert split_payment(D("300.00"), D("1000.00"), **{**kw, "on": date(2027, 1, 5)}).principal == D("300.00")
