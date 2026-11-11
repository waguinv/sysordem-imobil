import { NextResponse, type NextRequest } from "next/server";
import { decimalBR, toCSV } from "@/lib/csv";
import { api, ApiError } from "@/lib/session";
import type { CashFlow, Delinquency, SalesReport } from "@/lib/types";

/** Exportação CSV dos relatórios (a API valida as permissões com o token da sessão). */
export async function GET(req: NextRequest) {
  const tipo = req.nextUrl.searchParams.get("tipo");
  try {
    let csv: string;
    if (tipo === "fluxo") {
      const r = await api<CashFlow>("/reports/cash-flow?months_back=12&months_ahead=12");
      csv = toCSV(
        ["Mês", "Recebido", "Pago", "Saldo do mês", "A receber (em aberto)", "A pagar (em aberto)"],
        r.months.map((m) => [
          m.month,
          decimalBR(m.received),
          decimalBR(m.paid),
          decimalBR(m.net),
          decimalBR(m.expected_in),
          decimalBR(m.expected_out),
        ]),
      );
    } else if (tipo === "inadimplencia") {
      const r = await api<Delinquency>("/reports/delinquency");
      csv = toCSV(
        [
          "Contrato",
          "Comprador",
          "Telefone",
          "Empreendimento",
          "Quadra",
          "Lote",
          "Parcelas vencidas",
          "Valor vencido",
          "Vencimento mais antigo",
          "Dias de atraso",
          "Faixa",
        ],
        r.items.map((i) => [
          i.contract_number,
          i.customer_name,
          i.customer_phone,
          i.development_name,
          i.block_code,
          i.lot_number,
          i.overdue_count,
          decimalBR(i.overdue_amount),
          i.oldest_due,
          i.days_late,
          i.bucket,
        ]),
      );
    } else if (tipo === "vendas") {
      const qs = new URLSearchParams();
      for (const k of ["from", "to"]) {
        const v = req.nextUrl.searchParams.get(k);
        if (v && /^\d{4}-\d{2}-\d{2}$/.test(v)) qs.set(k, v);
      }
      const r = await api<SalesReport>(`/reports/sales${qs.size ? `?${qs}` : ""}`);
      csv = toCSV(
        ["Empreendimento", "Contratos assinados", "Valor total", "Entradas", "Distratos"],
        r.rows.map((x) => [
          x.development_name,
          x.contracts,
          decimalBR(x.total_value),
          decimalBR(x.down_payments),
          x.rescissions,
        ]),
      );
    } else {
      return NextResponse.json({ error: "tipo inválido" }, { status: 400 });
    }
    return new NextResponse(csv, {
      headers: {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": `attachment; filename="sysordem-${tipo}-${new Date().toISOString().slice(0, 10)}.csv"`,
        "Cache-Control": "no-store",
      },
    });
  } catch (e) {
    if (e instanceof ApiError) return NextResponse.json({ error: e.message }, { status: e.status });
    throw e;
  }
}
