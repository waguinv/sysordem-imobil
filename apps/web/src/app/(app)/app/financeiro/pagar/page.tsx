import { Receipt, Search } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Stat } from "@/components/finance/stat";
import { ActionForm, Field, selectClass } from "@/components/ui/action-form";
import {
  Badge,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Input,
} from "@/components/ui/primitives";
import { createPayableAction } from "@/lib/actions-fase3b";
import { brl, dateBR, PAYABLE_CATEGORY, payableStatus } from "@/lib/format";
import { api, can, getMe } from "@/lib/session";
import type { Development, Payable, PayablesSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Contas a pagar" };

const FILTERS = [
  { key: "aberta", label: "Em aberto" },
  { key: "vencida", label: "Vencidas" },
  { key: "paga", label: "Pagas" },
  { key: "cancelada", label: "Canceladas" },
];

const MANUAL = ["fornecedor", "obra", "imposto", "marketing", "outro"];

export default async function PagarPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string; q?: string }>;
}) {
  const me = await getMe();
  if (!can(me, "finance:view")) redirect("/app");
  const { status, q = "" } = await searchParams;
  const valid = FILTERS.some((f) => f.key === status) ? status! : "aberta";
  const qs = new URLSearchParams({ status: valid });
  if (q) qs.set("q", q);
  const manage = can(me, "finance:manage");
  const [summary, items, developments] = await Promise.all([
    api<PayablesSummary>("/payables/summary"),
    api<Payable[]>(`/payables?${qs}`),
    manage ? api<Development[]>("/developments") : Promise.resolve([] as Development[]),
  ]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-brand-institutional text-2xl font-bold">Contas a pagar</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Fornecedores, obras e impostos são lançados aqui. Comissões e repasses ao proprietário nascem em{" "}
          <Link href="/app/comissoes" className="text-brand-institutional underline">
            Comissões e repasses
          </Link>
          .
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Vencido"
          value={brl(summary.overdue_total)}
          hint={`${summary.overdue_count} conta(s)`}
          tone={summary.overdue_count > 0 ? "danger" : undefined}
        />
        <Stat label="Vence em 30 dias" value={brl(summary.due_30_total)} hint="contas em aberto" />
        <Stat
          label="Total em aberto"
          value={brl(summary.open_total)}
          hint={`${summary.open_count} conta(s)`}
        />
        <Stat
          label="Comissões e repasses a acertar"
          value={brl(summary.pending_participations)}
          hint={`pago no mês: ${brl(summary.paid_month)}`}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <nav className="flex flex-wrap gap-2" aria-label="Filtrar contas">
              {FILTERS.map((f) => (
                <Link
                  key={f.key}
                  href={`/app/financeiro/pagar?status=${f.key}`}
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
              <Input name="q" defaultValue={q} placeholder="Favorecido ou descrição" className="pl-9" />
            </form>
          </div>
          <Card>
            <CardContent>
              {items.length === 0 ? (
                <div className="flex flex-col items-center gap-2 py-8 text-center">
                  <Receipt className="text-brand-muted size-8" />
                  <p className="text-muted-foreground text-sm">Nenhuma conta nesta lista.</p>
                </div>
              ) : (
                <ul className="divide-y">
                  {items.map((p) => {
                    const st = payableStatus(p);
                    return (
                      <li key={p.id}>
                        <Link
                          href={`/app/financeiro/pagar/${p.id}`}
                          className="hover:bg-accent/60 -mx-2 flex flex-wrap items-center justify-between gap-2 rounded-lg px-2 py-3"
                        >
                          <div className="min-w-0">
                            <p className="truncate text-sm font-medium">{p.payee_name}</p>
                            <p className="text-muted-foreground truncate text-xs">
                              {PAYABLE_CATEGORY[p.category] ?? p.category} · {p.description}
                              {p.development_name && ` · ${p.development_name}`}
                            </p>
                          </div>
                          <div className="flex items-center gap-3 text-sm">
                            <span className="text-right tabular-nums">
                              {brl(p.amount)}
                              <span className="text-muted-foreground block text-xs">
                                {p.status === "paga"
                                  ? `pago ${dateBR(p.paid_on)}`
                                  : `vence ${dateBR(p.due_date)}`}
                              </span>
                            </span>
                            <Badge variant={st.badge}>{st.label}</Badge>
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

        {manage && (
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Nova conta</CardTitle>
                <CardDescription>Pagamentos da empresa a terceiros.</CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              <ActionForm action={createPayableAction} submitLabel="Cadastrar">
                <Field label="Favorecido" htmlFor="payee_name">
                  <Input id="payee_name" name="payee_name" required minLength={2} maxLength={160} />
                </Field>
                <Field label="Descrição" htmlFor="description">
                  <Input id="description" name="description" required minLength={3} maxLength={240} />
                </Field>
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Categoria" htmlFor="category">
                    <select id="category" name="category" className={selectClass} defaultValue="fornecedor">
                      {MANUAL.map((c) => (
                        <option key={c} value={c}>
                          {PAYABLE_CATEGORY[c]}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Valor (R$)" htmlFor="amount">
                    <Input id="amount" name="amount" inputMode="decimal" required />
                  </Field>
                  <Field label="Vencimento" htmlFor="due_date">
                    <Input id="due_date" name="due_date" type="date" required />
                  </Field>
                  <Field label="Empreendimento" htmlFor="development_id">
                    <select id="development_id" name="development_id" className={selectClass} defaultValue="">
                      <option value="">— geral —</option>
                      {developments.map((d) => (
                        <option key={d.id} value={d.id}>
                          {d.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>
                <Field label="Observações" htmlFor="notes">
                  <Input id="notes" name="notes" maxLength={2000} />
                </Field>
              </ActionForm>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
