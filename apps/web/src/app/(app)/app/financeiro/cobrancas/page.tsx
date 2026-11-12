import { QrCode, Search } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Stat } from "@/components/finance/stat";
import { Badge, Card, CardContent, Input } from "@/components/ui/primitives";
import { brl, CHARGE_METHOD, CHARGE_STATUS, dateBR, installmentLabel } from "@/lib/format";
import { api, can, getMe } from "@/lib/session";
import type { Charge, ChargesSummary, PaymentProviderInfo } from "@/lib/types";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Cobranças" };

const FILTERS = [
  { key: "pendente", label: "Aguardando" },
  { key: "paga", label: "Pagas" },
  { key: "divergente", label: "Divergentes" },
  { key: "todas", label: "Todas" },
];

export default async function CobrancasPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string; q?: string }>;
}) {
  const me = await getMe();
  if (!can(me, "finance:view")) redirect("/app");
  const { status, q = "" } = await searchParams;
  const valid = FILTERS.some((f) => f.key === status) ? status! : "pendente";
  const qs = new URLSearchParams();
  if (valid !== "todas") qs.set("status", valid);
  if (q) qs.set("q", q);
  const [summary, items, providers] = await Promise.all([
    api<ChargesSummary>("/charges/summary"),
    api<Charge[]>(`/charges?${qs}`),
    api<PaymentProviderInfo[]>("/payment-providers"),
  ]);
  const sandbox = providers.some((p) => p.sandbox);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-brand-institutional text-2xl font-bold">Cobranças Pix e boleto</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Emita a cobrança na tela da parcela (
          <Link href="/app/financeiro/receber" className="text-brand-institutional underline">
            Contas a receber
          </Link>
          ). A baixa é automática quando o PSP confirma o pagamento.
        </p>
      </div>

      {sandbox && (
        <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          Ambiente de <strong>sandbox</strong>: cobranças simuladas, sem dinheiro real. O PSP definitivo
          depende da escolha do cliente.
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-3">
        <Stat
          label="Aguardando pagamento"
          value={brl(summary.pending_total)}
          hint={`${summary.pending_count} cobrança(s)`}
        />
        <Stat
          label="Pago no mês"
          value={brl(summary.paid_month_total)}
          hint={`${summary.paid_month_count} cobrança(s)`}
        />
        <Stat
          label="Divergências"
          value={brl(summary.divergent_total)}
          hint={`${summary.divergent_count} pagamento(s) a tratar`}
          tone={summary.divergent_count > 0 ? "danger" : undefined}
        />
      </div>

      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <nav className="flex flex-wrap gap-2" aria-label="Filtrar cobranças">
            {FILTERS.map((f) => (
              <Link
                key={f.key}
                href={`/app/financeiro/cobrancas?status=${f.key}`}
                aria-current={valid === f.key ? "page" : undefined}
                className={cn(
                  "rounded-full border px-3 py-1 text-sm",
                  valid === f.key
                    ? "bg-brand-institutional border-brand-institutional text-white"
                    : "hover:bg-accent bg-white",
                )}
              >
                {f.label}
              </Link>
            ))}
          </nav>
          <form className="relative w-full sm:w-64" role="search">
            <input type="hidden" name="status" value={valid} />
            <Search className="text-muted-foreground absolute top-3 left-3 size-4" />
            <Input name="q" defaultValue={q} placeholder="Comprador ou contrato" className="pl-9" />
          </form>
        </div>
        <Card>
          <CardContent>
            {items.length === 0 ? (
              <div className="flex flex-col items-center gap-2 py-8 text-center">
                <QrCode className="text-brand-muted size-8" />
                <p className="text-muted-foreground text-sm">Nenhuma cobrança nesta lista.</p>
              </div>
            ) : (
              <ul className="divide-y">
                {items.map((c) => {
                  const st = CHARGE_STATUS[c.status];
                  return (
                    <li key={c.id}>
                      <Link
                        href={`/app/financeiro/cobrancas/${c.id}`}
                        className="hover:bg-accent/60 -mx-2 flex flex-wrap items-center justify-between gap-2 rounded-lg px-2 py-3"
                      >
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">{c.customer_name}</p>
                          <p className="text-muted-foreground truncate text-xs">
                            {CHARGE_METHOD[c.method]} · Contrato {c.contract_number} ·{" "}
                            {installmentLabel(c.installment_number)}
                          </p>
                        </div>
                        <div className="flex items-center gap-3 text-sm">
                          <span className="text-right tabular-nums">
                            {brl(c.paid_amount ?? c.amount)}
                            <span className="text-muted-foreground block text-xs">
                              {c.paid_on ? `pago ${dateBR(c.paid_on)}` : `vence ${dateBR(c.due_date)}`}
                            </span>
                          </span>
                          <Badge variant={st?.badge ?? "muted"}>{st?.label ?? c.status}</Badge>
                        </div>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
