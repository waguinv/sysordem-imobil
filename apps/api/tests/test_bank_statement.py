"""Leitura de extratos (unitário, sem banco): OFX 1.x/2.x e CSV brasileiro."""

from datetime import date
from decimal import Decimal

import pytest

from app.services.bank_statement import StatementParseError, parse, parse_amount, parse_date

D = Decimal

OFX_SGML = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
CHARSET:1252

<OFX>
<BANKMSGSRSV1><STMTTRNRS><STMTRS>
<BANKACCTFROM><BANKID>999<ACCTID>12345-6<ACCTTYPE>CHECKING</BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20261110120000[-3:BRT]
<TRNAMT>20000.00
<FITID>A1
<MEMO>PIX RECEBIDO MARIA
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20261111
<TRNAMT>-35,90
<FITID>A2
<NAME>TARIFA
<MEMO>PACOTE SERVICOS
</STMTTRN>
</BANKTRANLIST>
</STMTRS></STMTTRNRS></BANKMSGSRSV1>
</OFX>
"""

OFX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<?OFX OFXHEADER="200" VERSION="220"?>
<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKTRANLIST>
<STMTTRN><TRNTYPE>CREDIT</TRNTYPE><DTPOSTED>20261105</DTPOSTED><TRNAMT>150.5</TRNAMT><MEMO>TED</MEMO></STMTTRN>
<STMTTRN><TRNTYPE>CREDIT</TRNTYPE><DTPOSTED>20261105</DTPOSTED><TRNAMT>150.5</TRNAMT><MEMO>TED</MEMO></STMTTRN>
</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>
"""


def test_ofx_sgml():
    st = parse(OFX_SGML)
    assert st.format == "ofx" and st.account_hint == "12345-6"
    a, b = st.lines
    assert (a.posted_on, a.amount, a.description, a.fit_id) == (
        date(2026, 11, 10),
        D("20000.00"),
        "PIX RECEBIDO MARIA",
        "ofx:A1",
    )
    assert (b.amount, b.description) == (D("-35.90"), "TARIFA PACOTE SERVICOS")


def test_ofx_xml_without_fitid_keeps_identical_lines_apart():
    a, b = parse(OFX_XML).lines
    assert a.amount == b.amount == D("150.50") and a.fit_id != b.fit_id
    assert parse(OFX_XML).lines[0].fit_id == a.fit_id  # estável entre importações


def test_csv_brazilian_with_title_rows_and_balance():
    csv = (
        "﻿Extrato conta 12345\n"
        "\n"
        "Data;Histórico;Valor\n"
        "01/11/2026;SALDO ANTERIOR;1.000,00\n"
        "03/11/2026;PIX RECEBIDO;1.234,56\n"
        "03/11/2026;PIX RECEBIDO;1.234,56\n"
        "04/11/2026;PAGTO FORNECEDOR;-500,00\n"
        "05/11/2026;SALDO DO DIA;1.969,12\n"
    )
    st = parse(csv)
    assert st.format == "csv"
    assert [(ln.posted_on.day, ln.amount) for ln in st.lines] == [
        (3, D("1234.56")),
        (3, D("1234.56")),
        (4, D("-500.00")),
    ]
    assert st.lines[0].fit_id != st.lines[1].fit_id
    assert [ln.fit_id for ln in parse(csv).lines] == [ln.fit_id for ln in st.lines]


def test_csv_credit_debit_columns_and_ids():
    csv = (
        "date,description,credito,debito,documento\n"
        "2026-11-03,Recebimento,100.00,,77\n"
        "2026-11-04,Taxa,,12.5,78\n"
    )
    a, b = parse(csv).lines
    assert (a.amount, b.amount) == (D("100.00"), D("-12.50"))
    assert a.fit_id.startswith("csv:77|")


@pytest.mark.parametrize(
    ("content", "msg"),
    [
        ("foo;bar\n1;2\n", "Formato não reconhecido"),
        ("Data;Descrição;Valor\n31/02/2026;x;10,00\n", "Linha 2: data inválida"),
        ("Data;Descrição;Valor\n01/02/2026;x;dez reais\n", "Linha 2: valor inválido"),
        ("Data;Descrição;Valor\n01/02/2026;x;10,001\n", "valor inválido"),
        ("Data;Descrição;Valor\n01/02/2026;SALDO;10,00\n", "Nenhum lançamento"),
        ("<OFX><STMTTRN><TRNAMT>10</STMTTRN></OFX>", "sem data ou valor"),
    ],
)
def test_invalid_files(content, msg):
    with pytest.raises(StatementParseError, match=msg):
        parse(content)


def test_amount_and_date_formats():
    assert parse_amount("R$ 1.234,56") == D("1234.56")
    assert parse_amount("1,234.56") == D("1234.56")
    assert parse_amount("(10,00)") == D("-10.00")
    assert parse_amount("10,00 D") == D("-10.00")
    assert parse_amount("10,00-") == D("-10.00")
    assert parse_date("05/11/26") == date(2026, 11, 5)
    assert parse_date("2026-11-05T10:00:00") == date(2026, 11, 5)
