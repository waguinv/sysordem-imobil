import { ArrowLeft, Receipt } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ChargePanel } from "@/components/finance/charge-panel";
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
import { registerPaymentAction, reversePaymentAction } from "@/lib/actions-fase3a";
import {
  brl,
  dateBR,
  dateTime,
  installmentLabel,
  installmentStatus,
  PAYMENT_METHOD,
  pct,
} from "@/lib/format";
import { api, ApiError, can, getMe } from "@/lib/session";
import type { Charge, InstallmentDetail, PaymentProviderInfo } from "@/lib/types";

export const metadata: Metadata = { title: "Parcela" };

const br = (v: string) => v.replace(".", ",");

export default async function ParcelaPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const me = await getMe();
  let i: InstallmentDetail;
  try {
    i = await api<InstallmentDetail>(`/installments/${encodeURIComponent(id)}`);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 422 || e.status === 403)) notFound();
    throw e;
  }
  const [charges, providers] = await Promise.all([
    api<Charge[]>(`/charges?installment_id=${encodeURIComponent(i.id)}`),
    api<PaymentProviderInfo[]>("/payment-providers"),
  ]);
  const st = installmentStatus(i);
  const open = i.status === "aberta";
  const q = i.quote;
  const late = q.days_late > 0;

  const rows: [string, string][] = [
    ["Vencimento", dateBR(i.due_date)],
    ["Valor da parcela", brl(i.amount)],
    ["Amortização", brl(i.principal)],
    ["Juros do financiamento", brl(i.interest)],
    ["Já pago", brl(i.paid_principal)],
    ["Em aberto", brl(i.remaining)],
  ];

  return (
    <div className="space-y-6">
      <Link
        href={`/app/contratos/${i.contract_id}`}
        className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-sm"
      >
        <ArrowLeft className="size-4" /> Contrato {i.contract_number}
      </Link>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-brand-institutional text-2xl font-bold">
            {installmentLabel(i.number)} · {i.customer_name}
          </h1>
          <p className="text-muted-foreground text-sm">
            {i.development_name} · Quadra {i.block_code} · Lote {i.lot_number}
          </p>
        </div>
        <Badge variant={st.badge}>{st.label}</Badge>
      </div>

      <Card>
        <CardContent>
          <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-3 lg:grid-cols-6">
            {rows.map(([k, v]) => (
              <div key={k}>
                <dt className="text-muted-foreground text-xs">{k}</dt>
                <dd className="font-medium tabular-nums">{v}</dd>
              </div>
            ))}
          </dl>
        </CardContent>
      </Card>

      {open && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card className={late ? "border-red-200" : undefined}>
            <CardHeader>
              <div>
                <CardTitle>Para quitar hoje</CardTitle>
                <CardDescription>
                  {late
                    ? `${q.days_late} dias de atraso: multa de ${pct(q.late_fee_pct)} e juros de mora de ${pct(q.late_interest_monthly_pct)} a.m. (proporcionais aos dias).`
                    : "Em dia: sem multa nem juros de mora."}
                </CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              <dl className="space-y-1.5 text-sm">
                <div className="flex justify-between">
                  <dt>Parcela em aberto</dt>
                  <dd className="tabular-nums">{brl(q.remaining)}</dd>
                </div>
                {late && (
                  <>
                    <div className="flex justify-between">
                      <dt>Multa</dt>
                      <dd className="tabular-nums">{brl(q.late_fee)}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt>Juros de mora</dt>
                      <dd className="tabular-nums">{brl(q.late_interest)}</dd>
                    </div>
                  </>
                )}
                <div className="flex justify-between border-t pt-1.5 font-semibold">
                  <dt>Total</dt>
                  <dd className="tabular-nums">{brl(q.total)}</dd>
                </div>
              </dl>
              <p className="text-muted-foreground mt-3 text-xs">
                Correção monetária ainda não é aplicada (depende da fonte dos índices). Encargos devem ser
                validados por profissional habilitado.
              </p>
            </CardContent>
          </Card>

          {can(me, "finance:manage") && (
            <Card>
              <CardHeader>
                <div>
                  <CardTitle>Registrar recebimento</CardTitle>
                  <CardDescription>
                    Pode ser parcial. Em atraso, o sistema separa multa e juros da parte quitada.
                  </CardDescription>
                </div>
              </CardHeader>
              <CardContent>
                <ActionForm
                  key={`${i.payments.length}-${i.paid_principal}`}
                  action={registerPaymentAction.bind(null, i.id)}
                  submitLabel="Registrar"
                  resetOnSuccess
                >
                  <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Valor recebido (R$)" htmlFor="amount">
                      <Input
                        id="amount"
                        name="amount"
                        inputMode="decimal"
                        defaultValue={br(q.total)}
                        required
                      />
                    </Field>
                    <Field label="Data do recebimento" htmlFor="received_on">
                      <Input id="received_on" name="received_on" type="date" defaultValue={q.on} max={q.on} />
                    </Field>
                    <Field label="Forma" htmlFor="method">
                      <select id="method" name="method" className={selectClass} defaultValue="pix">
                        {Object.entries(PAYMENT_METHOD).map(([v, l]) => (
                          <option key={v} value={v}>
                            {l}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <Field label="Observação" htmlFor="notes">
                      <Input id="notes" name="notes" maxLength={2000} placeholder="ex.: comprovante 1234" />
                    </Field>
                  </div>
                  <p className="text-muted-foreground text-xs">
                    Data anterior a hoje recalcula os encargos para aquele dia.
                  </p>
                </ActionForm>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {(open || charges.length > 0) && (
        <ChargePanel
          installmentId={i.id}
          open={open}
          charges={charges}
          providers={providers}
          canManage={can(me, "finance:manage")}
          canResolve={can(me, "finance:reverse")}
        />
      )}

      <Card>
        <CardHeader>
          <div>
            <CardTitle className="flex items-center gap-2">
              <Receipt className="text-brand size-4" /> Recebimentos
            </CardTitle>
            <CardDescription>Lançamentos não são apagados nem editados: corrija com estorno.</CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          {i.payments.length === 0 ? (
            <p className="text-muted-foreground text-sm">Nenhum recebimento registrado.</p>
          ) : (
            <ul className="divide-y">
              {i.payments.map((p) => (
                <li key={p.id} className="space-y-2 py-3 text-sm">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span className={p.reversed ? "text-muted-foreground line-through" : undefined}>
                      <strong className="tabular-nums">{brl(p.amount)}</strong> em {dateBR(p.received_on)} ·{" "}
                      {PAYMENT_METHOD[p.method] ?? p.method}
                      {(Number(p.late_fee) > 0 || Number(p.late_interest) > 0) &&
                        ` · parcela ${brl(p.principal)} + multa ${brl(p.late_fee)} + juros ${brl(p.late_interest)}`}
                    </span>
                    <span className="text-muted-foreground text-xs">
                      {p.created_by_name ?? "Sistema (PSP)"} · {dateTime(p.created_at)}
                    </span>
                  </div>
                  {p.notes && <p className="text-muted-foreground text-xs">{p.notes}</p>}
                  {p.reversed ? (
                    <p className="text-xs text-red-700">
                      Estornado {p.reversed_at && dateTime(p.reversed_at)} · {p.reversal_reason}
                    </p>
                  ) : (
                    can(me, "finance:reverse") && (
                      <details>
                        <summary className="text-brand-institutional cursor-pointer text-xs font-medium">
                          Estornar
                        </summary>
                        <ActionForm
                          action={reversePaymentAction.bind(null, p.id)}
                          submitLabel="Confirmar estorno"
                          submitVariant="outline"
                          className="mt-2 sm:max-w-sm"
                        >
                          <Field label="Motivo" htmlFor={`rev-${p.id}`}>
                            <Input
                              id={`rev-${p.id}`}
                              name="reason"
                              required
                              minLength={3}
                              maxLength={500}
                              placeholder="ex.: cheque devolvido"
                            />
                          </Field>
                        </ActionForm>
                      </details>
                    )
                  )}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
