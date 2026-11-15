"""PDF do quadro-resumo do contrato, enviado para assinatura eletrônica.

Contém as partes, o lote e as condições da versão vigente. O texto jurídico completo (cláusulas) de
cada empresa é decisão pendente — este documento identifica de forma inequívoca o que está sendo
assinado (número, versão e condições), e o SHA-256 do arquivo fica registrado no envio.

Os campos de assinatura ficam sempre na ÚLTIMA página, nas alturas usadas pelo adaptador Documenso
(72% e 84% da página).
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from fpdf import FPDF

from app.services.finance import money

BRAND = (0, 59, 45)  # #003B2D
ACCENT = (0, 201, 154)  # #00C99A
_INDEX = {"nenhum": "Sem correção", "IPCA": "IPCA", "IGPM": "IGP-M", "INCC": "INCC"}
_AMORT = {"price": "Tabela Price (parcelas iguais)", "sac": "SAC (parcelas decrescentes)"}


@dataclass(frozen=True)
class ContractDoc:
    company_name: str
    number: str
    version: int
    customer_name: str
    customer_document: str | None
    broker_name: str | None
    development_name: str
    block_code: str
    lot_number: str
    area_m2: Decimal
    total_price: Decimal
    down_payment: Decimal
    installments_count: int
    installment_amount: Decimal
    amortization: str
    interest_monthly_pct: Decimal
    first_due_date: date | None
    adjustment_index: str
    late_fee_pct: Decimal
    late_interest_monthly_pct: Decimal
    notes: str | None
    company_signer: str
    issued_on: date


def _brl(v: Decimal) -> str:
    s = f"{money(v):,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(v: Decimal) -> str:
    return f"{v.normalize():f}".replace(".", ",") + "%"


def _doc(d: str | None) -> str:
    if not d:
        return "não informado"
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    return d


def _latin1(text: str) -> str:
    # Fontes padrão do PDF são Latin-1: troca o que não existe nela (ex.: travessão) por equivalente.
    return (
        text.replace("—", "-")
        .replace("–", "-")
        .replace("•", "-")
        .encode("latin-1", "replace")
        .decode("latin-1")
    )


class _Pdf(FPDF):
    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Helvetica", size=8)
        self.set_text_color(110, 110, 110)
        self.cell(0, 6, f"Página {self.page_no()}/{{nb}}", align="C")


def render(d: ContractDoc) -> tuple[bytes, int]:
    """Gera o PDF. Devolve (bytes, número da última página — onde ficam as assinaturas)."""
    pdf = _Pdf(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_title(_latin1(f"Contrato {d.number} v{d.version}"))
    pdf.set_creator("SysOrdem Imobil")
    # Data fixa (a da emissão), não "agora": o mesmo contrato gera sempre os mesmos bytes e o mesmo SHA-256,
    # então a prévia confere com o documento enviado para assinatura.
    pdf.set_creation_date(datetime(d.issued_on.year, d.issued_on.month, d.issued_on.day, tzinfo=UTC))
    pdf.add_page()
    pdf.set_fill_color(*BRAND)
    pdf.rect(0, 0, 210, 26, "F")
    pdf.set_xy(12, 8)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 7, _latin1(d.company_name))
    pdf.set_xy(12, 15)
    pdf.set_font("Helvetica", size=10)
    pdf.cell(0, 6, _latin1(f"Quadro-resumo do contrato nº {d.number} - versão {d.version}"))
    pdf.set_text_color(20, 20, 20)
    pdf.set_y(34)

    def section(title: str) -> None:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(*BRAND)
        pdf.cell(0, 7, _latin1(title), new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(*ACCENT)
        pdf.line(pdf.l_margin, pdf.get_y(), 210 - pdf.r_margin, pdf.get_y())
        pdf.ln(1)
        pdf.set_text_color(20, 20, 20)

    def row(label: str, value: str) -> None:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(55, 6, _latin1(label))
        pdf.set_font("Helvetica", size=10)
        pdf.multi_cell(0, 6, _latin1(value), new_x="LMARGIN", new_y="NEXT")

    section("Partes")
    row("Vendedora", d.company_name)
    row("Comprador(a)", f"{d.customer_name} - CPF/CNPJ {_doc(d.customer_document)}")
    if d.broker_name:
        row("Corretor(a)", d.broker_name)

    section("Imóvel")
    row("Empreendimento", d.development_name)
    row("Quadra / Lote", f"{d.block_code} / {d.lot_number}")
    row("Área", f"{f'{d.area_m2:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')} m²")

    section("Condições de pagamento")
    row("Valor total", _brl(d.total_price))
    row("Entrada", _brl(d.down_payment))
    if d.installments_count == 0:
        row("Parcelas", "À vista (sem parcelas)")
    else:
        label = "Valor da parcela" if d.amortization == "price" else "1ª parcela"
        row("Parcelas", f"{d.installments_count}x - {label}: {_brl(d.installment_amount)}")
        row("Sistema", _AMORT.get(d.amortization, d.amortization))
        row("Juros do financiamento", f"{_pct(d.interest_monthly_pct)} ao mês")
        if d.first_due_date:
            row("1º vencimento", d.first_due_date.strftime("%d/%m/%Y"))
        row("Correção", _INDEX.get(d.adjustment_index, d.adjustment_index))
    row("Multa por atraso", _pct(d.late_fee_pct))
    row("Juros de mora", f"{_pct(d.late_interest_monthly_pct)} ao mês")
    if d.notes:
        row("Observações", d.notes)

    pdf.ln(3)
    pdf.set_font("Helvetica", "I", 8.5)
    pdf.set_text_color(90, 90, 90)
    pdf.multi_cell(
        0,
        4.5,
        _latin1(
            "Este quadro-resumo identifica as condições da versão indicada do contrato. A assinatura "
            "eletrônica vale apenas para esta versão; qualquer alteração gera nova versão e novo envio. "
            f"Emitido em {d.issued_on.strftime('%d/%m/%Y')}."
        ),
        new_x="LMARGIN",
        new_y="NEXT",
    )

    # Bloco de assinaturas na última página, nas alturas usadas pelo adaptador (72% e 84%).
    if pdf.get_y() > 297 * 0.66:
        pdf.add_page()
    pdf.set_text_color(20, 20, 20)
    for y_pct, who in (
        (0.72, f"Comprador(a): {d.customer_name}"),
        (0.84, f"Pela vendedora ({d.company_name}): {d.company_signer}"),
    ):
        y = 297 * y_pct + 7
        pdf.set_draw_color(120, 120, 120)
        pdf.line(21, y, 95, y)
        pdf.set_xy(21, y + 1)
        pdf.set_font("Helvetica", size=9)
        pdf.cell(0, 5, _latin1(who))
    last_page = pdf.page_no()
    return bytes(pdf.output()), last_page
