import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { ActionForm, Field, selectClass } from "@/components/ui/action-form";
import { CopyButton } from "@/components/ui/copy-button";
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
  cancelSubscriptionAction,
  changePlanAction,
  payInvoiceAction,
  reactivateSubscriptionAction,
  simulateInvoiceAction,
} from "@/lib/actions-fase5c";
import { type Billing, limitLabel, SUBSCRIPTION_STATE } from "@/lib/billing";
import { brl, dateBR } from "@/lib/format";
import { api, can, getMe } from "@/lib/session";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Assinatura" };

function UsageBar({ label, used, max }: { label: string; used: number; max: number | null }) {
  const pct = max ? Math.min(100, Math.round((used / max) * 100)) : 0;
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-sm">
        <span>{label}</span>
        <span className="tabular-nums">
          {used.toLocaleString("pt-BR")} {max === null ? "(ilimitado)" : `de ${max.toLocaleString("pt-BR")}`}
        </span>
      </div>
      {max !== null && (
        <div className="bg-muted h-2 overflow-hidden rounded-full" aria-hidden="true">
          <div
            className={cn("h-full rounded-full", pct >= 90 ? "bg-amber-500" : "bg-brand")}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  );
}

export default async function AssinaturaPage() {
  const me = await getMe();
  if (!can(me, "billing:view")) redirect("/app");
  const b = await api<Billing>("/billing");
  const manage = can(me, "billing:manage");
  const st = b.status;
  const label = SUBSCRIPTION_STATE[st.state];
  const plan = b.plans.find((p) => p.code === st.plan_code);

  if (st.state === "sem_assinatura") {
    return (
      <div className="space-y-6">
        <h1 className="text-brand-institutional text-2xl font-bold">Assinatura</h1>
        <Card>
          <CardContent>
            <p className="text-sm">
              Esta empresa não tem assinatura cadastrada (conta criada antes dos planos ou pela SysOrdem): não
              há limites nem cobrança por aqui.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const open = b.invoices.filter((i) => i.status === "aberta");
  const history = b.invoices.filter((i) => i.status !== "aberta");

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-brand-institutional text-2xl font-bold">Assinatura</h1>
          <p className="text-muted-foreground text-sm">
            Plano {st.plan_name}
            {st.state === "teste" && st.covered_until && ` · teste grátis até ${dateBR(st.covered_until)}`}
            {st.state === "ativa" && st.covered_until && ` · pago até ${dateBR(st.covered_until)}`}
            {st.cancelled && " · cancelamento agendado"}
          </p>
        </div>
        <Badge variant={label.badge}>{label.label}</Badge>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Uso do plano</CardTitle>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <UsageBar
              label="Empreendimentos"
              used={b.usage.developments}
              max={plan?.max_developments ?? null}
            />
            <UsageBar label="Lotes" used={b.usage.lots} max={plan?.max_lots ?? null} />
            <UsageBar label="Usuários ativos" used={b.usage.users} max={plan?.max_users ?? null} />
          </CardContent>
        </Card>

        <Card className={open.length ? "border-amber-300" : undefined}>
          <CardHeader>
            <div>
              <CardTitle>Faturas em aberto</CardTitle>
              <CardDescription>
                {open.length
                  ? `Total: ${brl(b.amount_due)}. A fatura do mês aparece 7 dias antes do vencimento.`
                  : "Nada a pagar agora. A próxima fatura aparece 7 dias antes do vencimento."}
              </CardDescription>
            </div>
          </CardHeader>
          {open.length > 0 && (
            <CardContent>
              <ul className="divide-y">
                {open.map((i) => (
                  <li key={i.id} className="space-y-2 py-3 text-sm">
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <span>
                        {dateBR(i.period_start)} a {dateBR(i.period_end)} · vence {dateBR(i.due_date)}
                      </span>
                      <strong className="tabular-nums">{brl(i.amount)}</strong>
                    </div>
                    {i.pix_code || i.boleto_line ? (
                      <div className="bg-brand-surface/60 space-y-2 rounded-lg p-3">
                        <p className="font-mono text-xs break-all select-all">
                          {i.pix_code ?? i.boleto_line}
                        </p>
                        <div className="flex flex-wrap gap-2">
                          <CopyButton
                            text={(i.pix_code ?? i.boleto_line)!}
                            label={i.pix_code ? "Copiar Pix" : "Copiar linha"}
                          />
                          {b.sandbox && manage && (
                            <ActionForm
                              action={simulateInvoiceAction.bind(null, i.id)}
                              submitLabel="Simular pagamento (sandbox)"
                              submitVariant="outline"
                              className="space-y-1"
                            >
                              {null}
                            </ActionForm>
                          )}
                        </div>
                      </div>
                    ) : (
                      manage && (
                        <ActionForm
                          action={payInvoiceAction.bind(null, i.id)}
                          submitLabel="Gerar código para pagar"
                          className="flex flex-wrap items-center gap-2 space-y-0 [&>p]:w-full"
                        >
                          <select
                            name="method"
                            aria-label="Forma de pagamento"
                            className={`${selectClass} w-36`}
                            defaultValue="pix"
                          >
                            <option value="pix">Pix</option>
                            <option value="boleto">Boleto</option>
                          </select>
                        </ActionForm>
                      )
                    )}
                  </li>
                ))}
              </ul>
            </CardContent>
          )}
        </Card>
      </div>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Planos</CardTitle>
            <CardDescription>
              A troca vale na hora para os limites e no próximo pagamento para o valor. Para reduzir, o uso
              atual precisa caber no novo plano.
            </CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 md:grid-cols-3">
            {b.plans.map((p) => {
              const current = p.code === st.plan_code;
              return (
                <div
                  key={p.code}
                  className={cn("space-y-2 rounded-xl border p-4", current && "border-brand bg-brand/5")}
                >
                  <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                    <p className="font-semibold">{p.name}</p>
                    <p className="tabular-nums">
                      {brl(p.monthly_price)}
                      <span className="text-muted-foreground text-xs">/mês</span>
                    </p>
                  </div>
                  <ul className="text-muted-foreground space-y-0.5 text-xs">
                    <li>{limitLabel(p.max_developments, "empreendimentos")}</li>
                    <li>{limitLabel(p.max_lots, "lotes")}</li>
                    <li>{limitLabel(p.max_users, "usuários")}</li>
                  </ul>
                  {current ? (
                    <Badge>Plano atual</Badge>
                  ) : (
                    manage && (
                      <ActionForm
                        action={changePlanAction.bind(null, p.code)}
                        submitLabel={`Mudar para ${p.name}`}
                        submitVariant="outline"
                      >
                        {null}
                      </ActionForm>
                    )
                  )}
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {history.length > 0 && (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Histórico de faturas</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <ul className="divide-y">
              {history.map((i) => (
                <li key={i.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5 text-sm">
                  <span>
                    {dateBR(i.period_start)} a {dateBR(i.period_end)}
                    {i.paid_at && (
                      <span className="text-muted-foreground"> · paga em {dateBR(i.paid_at)}</span>
                    )}
                  </span>
                  <span className="flex items-center gap-2">
                    <span className="tabular-nums">{brl(i.amount)}</span>
                    <Badge variant={i.status === "paga" ? "default" : "muted"}>
                      {i.status === "paga" ? "Paga" : "Cancelada"}
                    </Badge>
                  </span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {manage && (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>{st.cancelled ? "Reativar assinatura" : "Cancelar assinatura"}</CardTitle>
              <CardDescription>
                {st.cancelled
                  ? "Volta a gerar as faturas mensais."
                  : "O acesso continua até o fim do período pago (ou do teste); depois o sistema fica só para consulta. Os dados não são apagados."}
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            {st.cancelled ? (
              <ActionForm action={reactivateSubscriptionAction} submitLabel="Reativar">
                {null}
              </ActionForm>
            ) : (
              <ActionForm
                action={cancelSubscriptionAction}
                submitLabel="Cancelar assinatura"
                submitVariant="destructive"
                className="sm:max-w-md"
              >
                <Field label="Motivo" htmlFor="cancel-reason">
                  <Input id="cancel-reason" name="reason" required minLength={3} maxLength={500} />
                </Field>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" name="confirm" required /> Quero cancelar a assinatura
                </label>
              </ActionForm>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
