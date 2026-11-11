import Link from "next/link";
import { Badge } from "@/components/ui/primitives";
import { brl, dateBR, installmentLabel, installmentStatus } from "@/lib/format";
import type { Installment } from "@/lib/types";

/** Tabela de parcelas. `showContract` mostra cliente/contrato (lista geral de contas a receber). */
export function InstallmentsTable({
  items,
  showContract = false,
  total,
}: {
  items: Installment[];
  showContract?: boolean;
  /** nº de parcelas do contrato, para exibir "3/120" */
  total?: number;
}) {
  return (
    <div className="-mx-2 overflow-x-auto">
      <table className="w-full min-w-[560px] text-sm">
        <thead>
          <tr className="text-muted-foreground border-b text-left text-xs">
            <th className="px-2 py-2 font-medium">{showContract ? "Cliente / contrato" : "Parcela"}</th>
            <th className="px-2 py-2 font-medium">Vencimento</th>
            <th className="px-2 py-2 text-right font-medium">Valor</th>
            <th className="px-2 py-2 text-right font-medium">Em aberto</th>
            <th className="px-2 py-2 font-medium">Situação</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {items.map((i) => {
            const st = installmentStatus(i);
            return (
              <tr key={i.id} className="hover:bg-accent/50">
                <td className="px-2 py-2.5">
                  <Link href={`/app/financeiro/parcelas/${i.id}`} className="font-medium hover:underline">
                    {showContract ? i.customer_name : installmentLabel(i.number, total)}
                  </Link>
                  {showContract && (
                    <span className="text-muted-foreground block text-xs">
                      {installmentLabel(i.number)} · contrato {i.contract_number} · Q{i.block_code} L
                      {i.lot_number}
                    </span>
                  )}
                </td>
                <td className="px-2 py-2.5 tabular-nums">
                  {dateBR(i.due_date)}
                  {i.overdue && <span className="block text-xs text-red-700">{i.days_late} dias</span>}
                </td>
                <td className="px-2 py-2.5 text-right tabular-nums">{brl(i.amount)}</td>
                <td className="px-2 py-2.5 text-right tabular-nums">
                  {i.status === "paga" ? "—" : brl(i.remaining)}
                </td>
                <td className="px-2 py-2.5">
                  <Badge variant={st.badge}>{st.label}</Badge>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
