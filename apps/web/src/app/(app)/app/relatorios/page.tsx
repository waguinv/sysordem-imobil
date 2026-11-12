import { Download } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Stat } from "@/components/finance/stat";
import {
  Button,
  buttonVariants,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Input,
} from "@/components/ui/primitives";
import { brl, dateBR } from "@/lib/format";
import { api, can, getMe } from "@/lib/session";
import type { CashFlow, Delinquency, SalesReport } from "@/lib/types";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Relatórios" };

type Tab = { key: string; label: string; perm: string };
const TABS: Tab[] = [
  { key: "fluxo", label: "Fluxo de caixa", perm: "finance:view" },
  { key: "inadimplencia", label: "Inadimplência", perm: "finance:view" },
  { key: "vendas", label: "Vendas", perm: "contracts:view" },
];

const MONTHS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
const monthLabel = (m: string) => `${MONTHS[Number(m.slice(5)) - 1]}/${m.slice(2, 4)}`;

function CsvLink({ href }: { href: string }) {
  return (
    <a href={href} className={buttonVariants({ variant: "outline" })}>
      <Download /> Exportar CSV
    </a>
  );
}

async function CashFlowTab() {
  const r = await api<CashFlow>("/reports/cash-flow?months_back=6&months_ahead=6");
  const current = new Date().toLocaleDateString("sv-SE", { timeZone: "America/Sao_Paulo" }).slice(0, 7);
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Fluxo de caixa mensal</CardTitle>
          <CardDescription>
            Realizado: recebimentos (com multa e juros, sem estornos) e contas pagas. Previsto: parcelas e
            contas em aberto pelo vencimento. Antes do período: {brl(r.overdue_in_before)} a receber e{" "}
            {brl(r.overdue_out_before)} a pagar vencidos.
          </CardDescription>
        </div>
        <CsvLink href="/app/relatorios/csv?tipo=fluxo" />
      </CardHeader>
      <CardContent>
        <div className="-mx-2 overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead>
              <tr className="text-muted-foreground border-b text-left text-xs">
                <th className="px-2 py-2 font-medium">Mês</th>
                <th className="px-2 py-2 text-right font-medium">Recebido</th>
                <th className="px-2 py-2 text-right font-medium">Pago</th>
                <th className="px-2 py-2 text-right font-medium">Saldo</th>
                <th className="px-2 py-2 text-right font-medium">A receber</th>
                <th className="px-2 py-2 text-right font-medium">A pagar</th>
              </tr>
            </thead>
            <tbody className="divide-y tabular-nums">
              {r.months.map((m) => (
                <tr key={m.month} className={cn(m.month === current && "bg-brand/5 font-medium")}>
                  <td className="px-2 py-2">{monthLabel(m.month)}</td>
                  <td className="px-2 py-2 text-right">{brl(m.received)}</td>
                  <td className="px-2 py-2 text-right">{brl(m.paid)}</td>
                  <td className={cn("px-2 py-2 text-right", Number(m.net) < 0 && "text-red-700")}>
                    {brl(m.net)}
                  </td>
                  <td className="text-muted-foreground px-2 py-2 text-right">{brl(m.expected_in)}</td>
                  <td className="text-muted-foreground px-2 py-2 text-right">{brl(m.expected_out)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

async function DelinquencyTab() {
  const r = await api<Delinquency>("/reports/delinquency");
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Stat
          label="Total vencido"
          value={brl(r.total)}
          hint={`${r.contracts} contrato(s), sem encargos`}
          tone={r.contracts ? "danger" : undefined}
        />
        {Object.entries(r.buckets).map(([k, v]) => (
          <Stat key={k} label={`${k} dias`} value={brl(v)} hint="pelo vencimento mais antigo" />
        ))}
      </div>
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Contratos em atraso</CardTitle>
            <CardDescription>Ordenados pelo atraso mais antigo. Dados pessoais: uso interno.</CardDescription>
          </div>
          <CsvLink href="/app/relatorios/csv?tipo=inadimplencia" />
        </CardHeader>
        <CardContent>
          {r.items.length === 0 ? (
            <p className="text-muted-foreground py-4 text-sm">Nenhum contrato em atraso.</p>
          ) : (
            <ul className="divide-y">
              {r.items.map((i) => (
                <li key={i.contract_id}>
                  <Link
                    href={`/app/contratos/${i.contract_id}`}
                    className="hover:bg-accent/60 -mx-2 flex flex-wrap items-center justify-between gap-2 rounded-lg px-2 py-3 text-sm"
                  >
                    <span className="min-w-0">
                      <strong className="font-medium">{i.customer_name}</strong>
                      {i.customer_phone && (
                        <span className="text-muted-foreground"> · {i.customer_phone}</span>
                      )}
                      <span className="text-muted-foreground block text-xs">
                        contrato {i.contract_number} · {i.development_name} · Q{i.block_code} L{i.lot_number}{" "}
                        · {i.overdue_count} parcela(s) desde {dateBR(i.oldest_due)}
                      </span>
                    </span>
                    <span className="text-right tabular-nums">
                      {brl(i.overdue_amount)}
                      <span className="block text-xs text-red-700">{i.days_late} dias</span>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

async function SalesTab({ from, to }: { from?: string; to?: string }) {
  const qs = new URLSearchParams();
  if (from) qs.set("from", from);
  if (to) qs.set("to", to);
  const r = await api<SalesReport>(`/reports/sales${qs.size ? `?${qs}` : ""}`);
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Vendas por empreendimento</CardTitle>
          <CardDescription>
            Contratos assinados de {dateBR(r.date_from)} a {dateBR(r.date_to)}: {r.contracts} contrato(s),{" "}
            {brl(r.total_value)}.
          </CardDescription>
        </div>
        <CsvLink href={`/app/relatorios/csv?tipo=vendas&from=${r.date_from}&to=${r.date_to}`} />
      </CardHeader>
      <CardContent className="space-y-4">
        <form className="flex flex-wrap items-end gap-2">
          <input type="hidden" name="tipo" value="vendas" />
          <label className="space-y-1 text-xs">
            <span className="text-muted-foreground block">De</span>
            <Input name="from" type="date" defaultValue={r.date_from} className="w-40" />
          </label>
          <label className="space-y-1 text-xs">
            <span className="text-muted-foreground block">Até</span>
            <Input name="to" type="date" defaultValue={r.date_to} className="w-40" />
          </label>
          <Button type="submit" variant="outline">
            Filtrar
          </Button>
        </form>
        {r.rows.length === 0 ? (
          <p className="text-muted-foreground text-sm">Nenhuma venda no período.</p>
        ) : (
          <div className="-mx-2 overflow-x-auto">
            <table className="w-full min-w-[520px] text-sm">
              <thead>
                <tr className="text-muted-foreground border-b text-left text-xs">
                  <th className="px-2 py-2 font-medium">Empreendimento</th>
                  <th className="px-2 py-2 text-right font-medium">Contratos</th>
                  <th className="px-2 py-2 text-right font-medium">Valor total</th>
                  <th className="px-2 py-2 text-right font-medium">Entradas</th>
                  <th className="px-2 py-2 text-right font-medium">Distratos</th>
                </tr>
              </thead>
              <tbody className="divide-y tabular-nums">
                {r.rows.map((x) => (
                  <tr key={x.development_id}>
                    <td className="px-2 py-2">{x.development_name}</td>
                    <td className="px-2 py-2 text-right">{x.contracts}</td>
                    <td className="px-2 py-2 text-right">{brl(x.total_value)}</td>
                    <td className="px-2 py-2 text-right">{brl(x.down_payments)}</td>
                    <td className="px-2 py-2 text-right">{x.rescissions}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default async function RelatoriosPage({
  searchParams,
}: {
  searchParams: Promise<{ tipo?: string; from?: string; to?: string }>;
}) {
  const me = await getMe();
  const tabs = TABS.filter((t) => can(me, t.perm));
  if (tabs.length === 0) redirect("/app");
  const { tipo, from, to } = await searchParams;
  const tab = tabs.find((t) => t.key === tipo) ?? tabs[0]!;
  const date = (v?: string) => (v && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : undefined);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-brand-institutional text-2xl font-bold">Relatórios</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Visões para gestão e contador. Exporte em CSV para abrir no Excel.
        </p>
      </div>
      <nav className="flex flex-wrap gap-2" aria-label="Relatório">
        {tabs.map((t) => (
          <Link
            key={t.key}
            href={`/app/relatorios?tipo=${t.key}`}
            aria-current={tab.key === t.key ? "page" : undefined}
            className={cn(
              "rounded-full border px-3 py-1 text-sm",
              tab.key === t.key
                ? "bg-brand-institutional border-brand-institutional text-white"
                : "hover:bg-accent bg-white",
            )}
          >
            {t.label}
          </Link>
        ))}
      </nav>
      {tab.key === "fluxo" && <CashFlowTab />}
      {tab.key === "inadimplencia" && <DelinquencyTab />}
      {tab.key === "vendas" && <SalesTab from={date(from)} to={date(to)} />}
    </div>
  );
}
