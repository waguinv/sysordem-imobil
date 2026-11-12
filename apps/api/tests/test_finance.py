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
