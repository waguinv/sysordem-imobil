import { Landmark, Search } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Stat } from "@/components/finance/stat";
import { ActionForm, Field } from "@/components/ui/action-form";
import {
  Badge,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Input,
} from "@/components/ui/primitives";
import {
  autoReconcileAction,
  createBankAccountAction,
  importStatementAction,
  setBankAccountActiveAction,
} from "@/lib/actions-fase4b";
import { brl, dateBR, dateTime, LINE_STATUS } from "@/lib/format";
import { api, can, getMe } from "@/lib/session";
import type { BankAccount, BankStatement, ReconSummary, StatementLine } from "@/lib/types";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Conciliação bancária" };

const FILTERS = [
  { key: "pendente", label: "Pendentes" },
  { key: "conciliada", label: "Conciliadas" },
  { key: "ignorada", label: "Ignoradas" },
  { key: "todas", label: "Todas" },
];

function NewAccountForm() {
  return (
    <ActionForm action={createBankAccountAction} submitLabel="Cadastrar conta" resetOnSuccess>
      <Field label="Nome" htmlFor="acc-name">
        <Input
          id="acc-name"
          name="name"
          required
          minLength={2}
          maxLength={80}
          placeholder="ex.: Conta movimento"
        />
      </Field>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Banco" htmlFor="acc-bank">
          <Input id="acc-bank" name="bank_name" maxLength={80} />
        </Field>
        <Field label="Agência" htmlFor="acc-branch">
          <Input id="acc-branch" name="branch" maxLength={20} />
        </Field>
        <Field label="Conta" htmlFor="acc-number">
          <Input id="acc-number" name="account_number" maxLength={30} />
        </Field>
      </div>
      <p className="text-muted-foreground text-xs">Só identificação — o sistema nunca pede senha do banco.</p>
    </ActionForm>
  );
}

export default async function ConciliacaoPage({
  searchParams,
}: {
  searchParams: Promise<{ conta?: string; status?: string; q?: string }>;
}) {
  const me = await getMe();
  if (!can(me, "finance:view")) redirect("/app");
  const manage = can(me, "finance:manage");
  const { conta, status, q = "" } = await searchParams;
  const accounts = await api<BankAccount[]>("/bank-accounts");
  const account = accounts.find((a) => a.id === conta) ?? accounts.find((a) => a.is_active) ?? accounts[0];

  if (!account) {
    return (
      <div className="space-y-6">
        <h1 className="text-brand-institutional text-2xl font-bold">Conciliação bancária</h1>
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Cadastre a primeira conta bancária</CardTitle>
              <CardDescription>
                Depois, importe o extrato (OFX ou CSV) e o sistema casa os créditos com os recebimentos e os
                débitos com as contas pagas.
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            {manage ? (
              <NewAccountForm />
            ) : (
              <p className="text-muted-foreground text-sm">Peça ao financeiro para cadastrar.</p>
            )}
          </CardContent>
        </Card>
      </div>
    );
  }

  const valid = FILTERS.some((f) => f.key === status) ? status! : "pendente";
  const qs = new URLSearchParams({ account_id: account.id });
  if (valid !== "todas") qs.set("status", valid);
  if (q) qs.set("q", q);
  const [summary, lines, statements] = await Promise.all([
    api<ReconSummary>(`/bank-accounts/${account.id}/reconciliation-summary`),
    api<StatementLine[]>(`/statement-lines?${qs}`),
    api<BankStatement[]>(`/bank-accounts/${account.id}/statements`),
  ]);
  const base = `/app/financeiro/conciliacao?conta=${account.id}`;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-brand-institutional text-2xl font-bold">Conciliação bancária</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Créditos do extrato casam com recebimentos; débitos, com contas pagas. Automático só quando o par é
          único (mesmo valor, até 3 dias); o resto você confere aqui.
        </p>
      </div>

      {accounts.length > 1 && (
        <nav className="flex flex-wrap gap-2" aria-label="Conta bancária">
          {accounts.map((a) => (
            <Link
              key={a.id}
              href={`/app/financeiro/conciliacao?conta=${a.id}`}
              aria-current={a.id === account.id ? "page" : undefined}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm",
                a.id === account.id
                  ? "bg-brand-institutional border-brand-institutional text-white"
                  : "hover:bg-accent bg-white",
                !a.is_active && "opacity-60",
              )}
            >
              <Landmark className="size-3.5" /> {a.name}
            </Link>
          ))}
        </nav>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Pendentes no extrato"
          value={String(summary.pending_count)}
          hint={`créditos ${brl(summary.pending_credits)} · débitos ${brl(summary.pending_debits)}`}
          tone={summary.pending_count > 0 ? "danger" : undefined}
        />
        <Stat
          label="Conciliadas"
          value={String(summary.reconciled_count)}
          hint={`${summary.ignored_count} ignorada(s)`}
        />
        <Stat
          label="Recebimentos sem extrato"
          value={brl(summary.unmatched_payments_total)}
          hint={`${summary.unmatched_payments_count} desde ${dateBR(summary.since)} (todas as contas)`}
        />
        <Stat
          label="Pagamentos sem extrato"
          value={brl(summary.unmatched_payables_total)}
          hint={`${summary.unmatched_payables_count} desde ${dateBR(summary.since)} (todas as contas)`}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <nav className="flex flex-wrap gap-2" aria-label="Filtrar lançamentos">
              {FILTERS.map((f) => (
                <Link
                  key={f.key}
                  href={`${base}&status=${f.key}`}
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
              <input type="hidden" name="conta" value={account.id} />
              <input type="hidden" name="status" value={valid} />
              <Search className="text-muted-foreground absolute top-3 left-3 size-4" />
              <Input name="q" defaultValue={q} placeholder="Descrição no extrato" className="pl-9" />
            </form>
          </div>
          <Card>
            <CardContent>
              {lines.length === 0 ? (
                <div className="flex flex-col items-center gap-2 py-8 text-center">
                  <Landmark className="text-brand-muted size-8" />
                  <p className="text-muted-foreground text-sm">
                    {statements.length
                      ? "Nenhum lançamento nesta lista."
                      : "Importe o primeiro extrato desta conta."}
                  </p>
                </div>
              ) : (
                <ul className="divide-y">
                  {lines.map((ln) => {
                    const st = LINE_STATUS[ln.status];
                    const credit = Number(ln.amount) > 0;
                    return (
                      <li key={ln.id}>
                        <Link
                          href={`/app/financeiro/conciliacao/linhas/${ln.id}`}
                          className="hover:bg-accent/60 -mx-2 flex flex-wrap items-center justify-between gap-2 rounded-lg px-2 py-3"
                        >
                          <div className="min-w-0">
                            <p className="truncate text-sm font-medium">{ln.description}</p>
                            <p className="text-muted-foreground text-xs">
                              {dateBR(ln.posted_on)} · {credit ? "crédito" : "débito"}
                              {ln.matched_count > 1 && ` · ${ln.matched_count} lançamentos`}
                              {ln.ignored_reason && ` · ${ln.ignored_reason}`}
                            </p>
                          </div>
                          <div className="flex items-center gap-3 text-sm">
                            <span
                              className={cn(
                                "tabular-nums",
                                credit ? "text-brand-institutional" : "text-red-700",
                              )}
                            >
                              {brl(ln.amount)}
                            </span>
                            <Badge variant={st?.badge ?? "muted"}>{st?.label ?? ln.status}</Badge>
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

        <div className="space-y-4">
          {manage && account.is_active && (
            <Card>
              <CardHeader>
                <div>
                  <CardTitle>Importar extrato</CardTitle>
                  <CardDescription>
                    OFX do internet banking ou CSV com Data, Descrição e Valor. Reimportar períodos
                    sobrepostos não duplica.
                  </CardDescription>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                <ActionForm
                  action={importStatementAction.bind(null, account.id)}
                  submitLabel="Importar"
                  resetOnSuccess
                >
                  <Field label="Arquivo (máx. 2 MB)" htmlFor="file">
                    <Input id="file" name="file" type="file" accept=".ofx,.csv,.txt" required />
                  </Field>
                </ActionForm>
                <ActionForm
                  action={autoReconcileAction.bind(null, account.id)}
                  submitLabel="Conciliar automaticamente"
                  submitVariant="outline"
                >
                  <p className="text-muted-foreground text-xs">
                    Use depois de lançar recebimentos ou pagamentos que já estavam no extrato.
                  </p>
                </ActionForm>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader>
              <div>
                <CardTitle>Extratos importados</CardTitle>
                <CardDescription>{account.name}</CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              {statements.length === 0 ? (
                <p className="text-muted-foreground text-sm">Nenhum ainda.</p>
              ) : (
                <ul className="divide-y text-sm">
                  {statements.slice(0, 8).map((s) => (
                    <li key={s.id} className="py-2">
                      <p className="truncate font-medium">{s.file_name}</p>
                      <p className="text-muted-foreground text-xs">
                        {dateBR(s.period_start)} a {dateBR(s.period_end)} · {s.lines_new} novo(s) de{" "}
                        {s.lines_total} · {s.auto_matched} automático(s)
                      </p>
                      <p className="text-muted-foreground text-xs">
                        {s.imported_by_name ?? "—"} · {dateTime(s.created_at)}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          {manage && (
            <Card>
              <CardHeader>
                <div>
                  <CardTitle>Contas bancárias</CardTitle>
                  <CardDescription>
                    {[
                      account.name,
                      account.bank_name,
                      account.branch && `ag. ${account.branch}`,
                      account.account_number && `c/c ${account.account_number}`,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </CardDescription>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                <ActionForm
                  action={setBankAccountActiveAction.bind(null, account.id, !account.is_active)}
                  submitLabel={account.is_active ? `Desativar ${account.name}` : `Reativar ${account.name}`}
                  submitVariant="outline"
                >
                  {null}
                </ActionForm>
                <details>
                  <summary className="text-brand-institutional cursor-pointer text-sm font-medium">
                    Nova conta
                  </summary>
                  <div className="mt-3">
                    <NewAccountForm />
                  </div>
                </details>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
