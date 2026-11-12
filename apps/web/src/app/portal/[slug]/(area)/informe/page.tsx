import type { Metadata } from "next";
import Link from "next/link";
import { PrintButton } from "@/components/ui/print-button";
import { Card, CardContent } from "@/components/ui/primitives";
import { brl, dateBR, maskDoc } from "@/lib/format";
import { portalApi, type PortalStatement } from "@/lib/portal";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Informe de pagamentos" };

export default async function PortalStatementPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ ano?: string }>;
}) {
  const { slug } = await params;
  const { ano } = await searchParams;
  const year = ano && /^\d{4}$/.test(ano) ? ano : "";
  const s = await portalApi<PortalStatement>(slug, `/statement${year ? `?year=${year}` : ""}`);
  const thisYear = new Date().getFullYear();
  const years = [thisYear, thisYear - 1, thisYear - 2, thisYear - 3];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3 print:hidden">
        <h1 className="text-brand-institutional text-2xl font-bold">Informe de pagamentos</h1>
        <PrintButton />
      </div>
      <nav className="flex flex-wrap gap-2 print:hidden" aria-label="Ano">
        {years.map((y) => (
          <Link
            key={y}
            href={`/portal/${slug}/informe?ano=${y}`}
            aria-current={s.year === y ? "page" : undefined}
            className={cn(
              "rounded-full border px-3 py-1 text-sm",
              s.year === y
                ? "bg-brand-institutional border-brand-institutional text-white"
                : "hover:bg-accent bg-white",
            )}
          >
            {y}
          </Link>
        ))}
      </nav>
      <Card>
        <CardContent className="space-y-4">
          <div className="text-sm">
            <p className="text-lg font-semibold">Pagamentos de {s.year}</p>
            <p>
              Comprador: {s.customer_name}
              {s.customer_document && ` · ${maskDoc(s.customer_document)}`}
            </p>
            <p>Empresa: {s.company_name}</p>
          </div>
          {s.lines.length === 0 ? (
            <p className="text-muted-foreground text-sm">Nenhum pagamento registrado em {s.year}.</p>
          ) : (
            <div className="-mx-2 overflow-x-auto">
              <table className="w-full min-w-[480px] text-sm">
                <thead>
                  <tr className="text-muted-foreground border-b text-left text-xs">
                    <th className="px-2 py-2 font-medium">Data</th>
                    <th className="px-2 py-2 font-medium">Contrato</th>
                    <th className="px-2 py-2 font-medium">Parcela</th>
                    <th className="px-2 py-2 text-right font-medium">Valor pago</th>
                  </tr>
                </thead>
                <tbody className="divide-y tabular-nums">
                  {s.lines.map((l, i) => (
                    <tr key={i}>
                      <td className="px-2 py-2">{dateBR(l.received_on)}</td>
                      <td className="px-2 py-2">{l.contract_number}</td>
                      <td className="px-2 py-2">
                        {l.installment_number === 0 ? "Entrada" : l.installment_number}
                      </td>
                      <td className="px-2 py-2 text-right">{brl(l.amount)}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot className="font-semibold">
                  <tr className="border-t">
                    <td className="px-2 py-2" colSpan={3}>
                      Total pago em {s.year}
                      {Number(s.total_late_charges) > 0 && (
                        <span className="text-muted-foreground block text-xs font-normal">
                          inclui {brl(s.total_late_charges)} de multa e juros por atraso
                        </span>
                      )}
                    </td>
                    <td className="px-2 py-2 text-right">{brl(s.total)}</td>
                  </tr>
                </tfoot>
              </table>
            </div>
          )}
          <p className="text-muted-foreground text-xs">
            Valores efetivamente pagos no ano (pagamentos estornados não entram). Confirme com seu contador
            como declarar o imóvel e os pagamentos no Imposto de Renda.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
