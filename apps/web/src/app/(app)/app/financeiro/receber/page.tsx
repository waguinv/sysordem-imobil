import { Search, Wallet } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { InstallmentsTable } from "@/components/finance/installments-table";
import { Card, CardContent, Input } from "@/components/ui/primitives";
import { brl } from "@/lib/format";
import { api, can, getMe } from "@/lib/session";
import type { Installment, ReceivablesSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Contas a receber" };

const FILTERS = [
  { key: "vencida", label: "Vencidas" },
  { key: "a_vencer", label: "A vencer" },
  { key: "aberta", label: "Todas em aberto" },
  { key: "paga", label: "Pagas" },
];

function Stat({ label, value, hint, tone }: { label: string; value: string; hint: string; tone?: "danger" }) {
  return (
    <Card>
      <CardContent className="space-y-1">
        <p className="text-muted-foreground text-xs">{label}</p>
        <p
          className={cn(
            "text-xl font-bold tabular-nums",
            tone === "danger" ? "text-red-700" : "text-brand-institutional",
          )}
        >
          {value}
        </p>
        <p className="text-muted-foreground text-xs">{hint}</p>
      </CardContent>
    </Card>
  );
}

export default async function ReceberPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string; q?: string }>;
}) {
  const me = await getMe();
  if (!can(me, "finance:view")) redirect("/app");
  const { status, q = "" } = await searchParams;
  const [summary] = await Promise.all([api<ReceivablesSummary>("/receivables/summary")]);
  const fallback = summary.overdue_count > 0 ? "vencida" : "a_vencer";
  const valid = FILTERS.some((f) => f.key === status) ? status! : fallback;
  const qs = new URLSearchParams({ status: valid });
  if (q) qs.set("q", q);
  const items = await api<Installment[]>(`/installments?${qs}`);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-brand-institutional text-2xl font-bold">Contas a receber</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Parcelas dos contratos assinados. Abra uma parcela para registrar o recebimento (total ou parcial)
          ou estornar um lançamento.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Vencido"
          value={brl(summary.overdue_total)}
          hint={`${summary.overdue_count} parcela(s), sem encargos`}
          tone={summary.overdue_count > 0 ? "danger" : undefined}
        />
        <Stat
          label="Vence em 30 dias"
          value={brl(summary.due_30_total)}
          hint={`${summary.due_30_count} parcela(s)`}
        />
        <Stat
          label="Total em aberto"
          value={brl(summary.open_total)}
          hint={`${summary.open_count} parcela(s)`}
        />
        <Stat
          label="Recebido no mês"
          value={brl(summary.received_month)}
          hint={`inclui ${brl(summary.received_month_late_charges)} de multa e juros; sem estornos`}
        />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <nav className="flex flex-wrap gap-2" aria-label="Filtrar parcelas">
          {FILTERS.map((f) => (
            <Link
              key={f.key}
              href={`/app/financeiro/receber?status=${f.key}`}
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
        <form className="relative w-full sm:w-72" role="search">
          <input type="hidden" name="status" value={valid} />
          <Search className="text-muted-foreground absolute top-3 left-3 size-4" />
          <Input name="q" defaultValue={q} placeholder="Cliente ou nº do contrato" className="pl-9" />
        </form>
      </div>

      <Card>
        <CardContent>
          {items.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-8 text-center">
              <Wallet className="text-brand-muted size-8" />
              <p className="text-muted-foreground text-sm">Nenhuma parcela nesta lista.</p>
            </div>
          ) : (
            <InstallmentsTable items={items} showContract />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
