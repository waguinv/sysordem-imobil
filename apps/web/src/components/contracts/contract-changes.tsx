import { RefreshCcw, Undo2 } from "lucide-react";
import Link from "next/link";
import { ActionForm, Field, selectClass } from "@/components/ui/action-form";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Input } from "@/components/ui/primitives";
import { renegotiateAction, rescindAction } from "@/lib/actions-fase3c";
import { AMORTIZATION, brl, dateBR, dateTime, pct } from "@/lib/format";
import { api, can, type Me } from "@/lib/session";
import type {
  Contract,
  ContractPolicy,
  OpenBalance,
  Renegotiation,
  Rescission,
  RescissionQuote,
} from "@/lib/types";

function inDays(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() + n);
  return d.toLocaleDateString("sv-SE", { timeZone: "America/Sao_Paulo" });
}

/** Renegociação (saldo em aberto → novo cronograma) e distrato do contrato assinado. */
export async function ContractChanges({ c, me, policy }: { c: Contract; me: Me; policy: ContractPolicy }) {
  if (c.status === "distratado") {
    const r = await api<Rescission | null>(`/contracts/${c.id}/rescission`);
    if (!r) return null;
    return (
      <Card className="border-red-200">
        <CardHeader>
          <div>
            <CardTitle className="flex items-center gap-2">
              <Undo2 className="size-4 text-red-700" /> Distrato
            </CardTitle>
            <CardDescription>
              {dateTime(r.created_at)} por {r.author_name ?? "—"} · {r.reason}
            </CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
            <div>
              <dt className="text-muted-foreground text-xs">Pago pelo comprador</dt>
              <dd className="font-medium tabular-nums">{brl(r.paid_total)}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground text-xs">Retenção ({pct(r.retention_pct)} do contrato)</dt>
              <dd className="font-medium tabular-nums">{brl(r.retention_amount)}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground text-xs">Devolução</dt>
              <dd className="font-medium tabular-nums">{brl(r.refund_amount)}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground text-xs">Conta a pagar</dt>
              <dd className="font-medium">
                {r.payable_id && can(me, "finance:view") ? (
                  <Link href={`/app/financeiro/pagar/${r.payable_id}`} className="underline">
                    abrir devolução
                  </Link>
                ) : (
                  "—"
                )}
              </dd>
            </div>
          </dl>
        </CardContent>
      </Card>
    );
  }
  if (c.status !== "assinado" || !can(me, "finance:view")) return null;

  const canReneg = can(me, "finance:manage");
  const canRescind = can(me, "contracts:sign");
  const [balance, history, quote] = await Promise.all([
    api<OpenBalance>(`/contracts/${c.id}/open-balance`),
    api<Renegotiation[]>(`/contracts/${c.id}/renegotiations`),
    canRescind ? api<RescissionQuote>(`/contracts/${c.id}/rescission-quote`) : Promise.resolve(null),
  ]);

  return (
    <>
      {history.length > 0 && (
        <Card>
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-2">
                <RefreshCcw className="text-brand size-4" /> Renegociações
              </CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <ul className="divide-y">
              {history.map((r) => (
                <li key={r.id} className="py-2.5 text-sm">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span>
                      {r.replaced_count} parcela(s) · {brl(r.old_balance)}
                      {Number(r.late_charges) > 0 && ` + encargos ${brl(r.late_charges)}`}
                      {Number(r.discount) > 0 && ` − desconto ${brl(r.discount)}`} →{" "}
                      <strong>
                        {brl(r.new_balance)} em {r.installments_count}× ({AMORTIZATION[r.amortization]?.label}
                        , {pct(r.interest_monthly_pct)} a.m.)
                      </strong>{" "}
                      a partir de {dateBR(r.first_due_date)}
                    </span>
                    <span className="text-muted-foreground text-xs">
                      {r.author_name ?? "—"} · {dateTime(r.created_at)}
                    </span>
                  </div>
                  <p className="text-muted-foreground text-xs">{r.reason}</p>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {(canReneg && balance.count > 0) || quote ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {canReneg && balance.count > 0 && (
            <Card>
              <CardHeader>
                <div>
                  <CardTitle>Renegociar saldo</CardTitle>
                  <CardDescription>
                    {balance.count} parcela(s) em aberto somam {brl(balance.balance)}
                    {balance.overdue_count > 0 &&
                      ` (${balance.overdue_count} vencida(s), encargos de hoje: ${brl(balance.late_charges)})`}
                    . Elas viram “renegociada” e um novo cronograma é gerado.
                  </CardDescription>
                </div>
              </CardHeader>
              <CardContent>
                <ActionForm
                  action={renegotiateAction.bind(null, c.id, c.lot_id)}
                  submitLabel="Renegociar"
                  resetOnSuccess
                >
                  <div className="grid gap-3 sm:grid-cols-2">
                    <Field label={`Nº de parcelas (máx. ${policy.max_installments})`} htmlFor="rn-n">
                      <Input
                        id="rn-n"
                        name="installments_count"
                        type="number"
                        min={1}
                        max={policy.max_installments}
                        defaultValue={balance.count}
                        required
                      />
                    </Field>
                    <Field label="1º vencimento" htmlFor="rn-first">
                      <Input
                        id="rn-first"
                        name="first_due_date"
                        type="date"
                        defaultValue={inDays(30)}
                        required
                      />
                    </Field>
                    <Field
                      label={`Juros (% a.m.; máx. ${pct(policy.max_interest_monthly_pct)})`}
                      htmlFor="rn-i"
                    >
                      <Input
                        id="rn-i"
                        name="interest_monthly_pct"
                        inputMode="decimal"
                        defaultValue={c.terms.interest_monthly_pct.replace(".", ",")}
                      />
                    </Field>
                    <Field label="Sistema" htmlFor="rn-amort">
                      <select
                        id="rn-amort"
                        name="amortization"
                        className={selectClass}
                        defaultValue={c.terms.amortization}
                      >
                        {policy.allowed_amortizations.map((a) => (
                          <option key={a} value={a}>
                            {AMORTIZATION[a]?.label} ({AMORTIZATION[a]?.hint})
                          </option>
                        ))}
                      </select>
                    </Field>
                    <Field label="Desconto (R$)" htmlFor="rn-disc">
                      <Input id="rn-disc" name="discount" inputMode="decimal" defaultValue="0" />
                    </Field>
                    <label className="flex items-center gap-2 self-end pb-2 text-sm">
                      <input type="checkbox" name="include_late_charges" defaultChecked /> Incluir multa e
                      juros de mora
                    </label>
                  </div>
                  <Field label="Motivo" htmlFor="rn-reason">
                    <Input id="rn-reason" name="reason" required minLength={3} maxLength={500} />
                  </Field>
                </ActionForm>
              </CardContent>
            </Card>
          )}
          {quote && (
            <Card className="border-red-200">
              <CardHeader>
                <div>
                  <CardTitle>Distrato</CardTitle>
                  <CardDescription>
                    Pago até hoje {brl(quote.paid_total)} (parcelas, sem encargos). Retenção padrão de 10% do
                    valor do contrato ({brl(quote.retention_amount)}), limitada ao pago; devolução de{" "}
                    {brl(quote.refund_amount)}. As {quote.open_count} parcela(s) em aberto são canceladas e o
                    lote volta a ficar disponível. Valide com o jurídico.
                  </CardDescription>
                </div>
              </CardHeader>
              <CardContent>
                <ActionForm
                  action={rescindAction.bind(null, c.id, c.lot_id)}
                  submitLabel="Registrar distrato"
                  submitVariant="destructive"
                >
                  <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Retenção (% do contrato)" htmlFor="rs-pct">
                      <Input id="rs-pct" name="retention_pct" inputMode="decimal" defaultValue="10" />
                    </Field>
                    <Field label="Vencimento da devolução" htmlFor="rs-due">
                      <Input id="rs-due" name="refund_due_date" type="date" defaultValue={inDays(30)} />
                    </Field>
                  </div>
                  <Field label="Motivo" htmlFor="rs-reason">
                    <Input id="rs-reason" name="reason" required minLength={3} maxLength={500} />
                  </Field>
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" name="confirm" required /> Entendo que o distrato não pode ser
                    desfeito
                  </label>
                </ActionForm>
              </CardContent>
            </Card>
          )}
        </div>
      ) : null}
    </>
  );
}
