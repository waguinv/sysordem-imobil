import { ArrowLeft } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ActionForm, selectClass } from "@/components/ui/action-form";
import { CopyButton } from "@/components/ui/copy-button";
import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/components/ui/primitives";
import { portalChargeAction } from "@/lib/actions-portal";
import { ADJUSTMENT_INDEX, AMORTIZATION, brl, dateBR, PAYMENT_METHOD, pct } from "@/lib/format";
import { PortalError, portalApi, type PortalContractDetail, type PortalInstallment } from "@/lib/portal";

export const metadata: Metadata = { title: "Meu contrato" };

function statusOf(i: PortalInstallment): {
  label: string;
  badge: "default" | "muted" | "outline" | "danger";
} {
  if (i.status === "paga") return { label: "Paga", badge: "default" };
  if (i.status === "renegociada") return { label: "Renegociada", badge: "muted" };
  if (i.status === "cancelada") return { label: "Cancelada", badge: "muted" };
  return i.overdue
    ? { label: `${i.days_late} dias em atraso`, badge: "danger" }
    : { label: "A vencer", badge: "outline" };
}

export default async function PortalContractPage({
  params,
}: {
  params: Promise<{ slug: string; id: string }>;
}) {
  const { slug, id } = await params;
  let c: PortalContractDetail;
  try {
    c = await portalApi<PortalContractDetail>(slug, `/contracts/${encodeURIComponent(id)}`);
  } catch (e) {
    if (e instanceof PortalError && (e.status === 404 || e.status === 422)) notFound();
    throw e;
  }
  const openAll = c.installments.filter((i) => i.status === "aberta");
  const VISIBLE_OPEN = 6;
  const open = openAll.slice(0, VISIBLE_OPEN);
  const later = openAll.slice(VISIBLE_OPEN);
  const others = c.installments.filter((i) => i.status !== "aberta");
  const t = c.terms;

  return (
    <div className="space-y-6">
      <Link href={`/portal/${slug}`} className="text-muted-foreground inline-flex items-center gap-1 text-sm">
        <ArrowLeft className="size-4" /> Meus contratos
      </Link>
      <div>
        <h1 className="text-brand-institutional text-2xl font-bold">
          {c.development_name} · Quadra {c.block_code} · Lote {c.lot_number}
        </h1>
        <p className="text-muted-foreground text-sm">
          Contrato {c.number}
          {c.signed_at && ` · assinado em ${dateBR(c.signed_at)}`}
          {c.status === "distratado" && " · distratado"}
        </p>
      </div>

      <Card>
        <CardContent>
          <dl className="grid gap-3 text-sm sm:grid-cols-4">
            <div>
              <dt className="text-muted-foreground text-xs">Valor do contrato</dt>
              <dd className="font-medium tabular-nums">{brl(t.total_price)}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground text-xs">Entrada</dt>
              <dd className="font-medium tabular-nums">{brl(t.down_payment)}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground text-xs">Parcelamento</dt>
              <dd className="font-medium">
                {t.installments_count === 0
                  ? "À vista"
                  : `${t.installments_count}× · ${AMORTIZATION[t.amortization]?.label ?? t.amortization} · ${pct(t.interest_monthly_pct)} a.m.`}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground text-xs">Soma das parcelas em aberto</dt>
              <dd className="font-medium tabular-nums">{brl(c.open_balance)}</dd>
            </div>
          </dl>
          {c.renegotiated && (
            <p className="mt-3 text-sm text-amber-800">
              Este contrato foi renegociado: as parcelas abaixo seguem o novo plano combinado com a empresa.
            </p>
          )}
          <p className="text-muted-foreground mt-3 text-xs">
            Em atraso: multa de {pct(t.late_fee_pct)} e juros de {pct(t.late_interest_monthly_pct)} ao mês,
            proporcionais aos dias. Correção: {ADJUSTMENT_INDEX[t.adjustment_index] ?? t.adjustment_index}.
          </p>
        </CardContent>
      </Card>

      {open.length > 0 && (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Parcelas a pagar</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <ul className="divide-y">
              {open.map((i) => {
                const st = statusOf(i);
                const ch = i.pending_charge;
                return (
                  <li key={i.id} className="space-y-3 py-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div>
                        <p className="font-medium">{i.label}</p>
                        <p className="text-muted-foreground text-xs tabular-nums">
                          vence {dateBR(i.due_date)} · {brl(i.remaining)}
                          {i.remaining !== i.amount && ` (de ${brl(i.amount)})`}
                        </p>
                      </div>
                      <Badge variant={st.badge}>{st.label}</Badge>
                    </div>
                    {ch ? (
                      <div className="bg-brand-surface/60 space-y-2 rounded-lg p-3 text-sm">
                        <p>
                          {ch.method === "pix" ? "Pix" : "Boleto"} de <strong>{brl(ch.amount)}</strong>, vence{" "}
                          {dateBR(ch.due_date)}.
                        </p>
                        {ch.pix_code && (
                          <div className="space-y-1">
                            <p className="text-muted-foreground text-xs">Pix copia e cola</p>
                            <p className="font-mono text-xs break-all select-all">{ch.pix_code}</p>
                            <CopyButton text={ch.pix_code} label="Copiar código Pix" />
                          </div>
                        )}
                        {ch.boleto_line && (
                          <div className="space-y-1">
                            <p className="text-muted-foreground text-xs">Linha digitável</p>
                            <p className="font-mono text-xs break-all select-all">{ch.boleto_line}</p>
                            <CopyButton text={ch.boleto_line} label="Copiar linha digitável" />
                          </div>
                        )}
                        <p className="text-muted-foreground text-xs">
                          O pagamento aparece aqui quando o banco confirmar (pode levar alguns minutos).
                        </p>
                      </div>
                    ) : (
                      <ActionForm
                        action={portalChargeAction.bind(null, slug, c.id, i.id)}
                        submitLabel="Gerar código para pagar"
                        submitVariant="outline"
                        className="flex flex-wrap items-center gap-2 space-y-0 [&>p]:w-full"
                      >
                        <select
                          name="method"
                          aria-label="Forma de pagamento"
                          className={`${selectClass} w-40`}
                          defaultValue="pix"
                        >
                          <option value="pix">Pix</option>
                          <option value="boleto">Boleto</option>
                        </select>
                      </ActionForm>
                    )}
                  </li>
                );
              })}
            </ul>
            {later.length > 0 && (
              <details className="mt-2 border-t pt-3">
                <summary className="text-brand-institutional cursor-pointer text-sm font-medium">
                  Ver as outras {later.length} parcelas a vencer
                </summary>
                <ul className="mt-2 divide-y">
                  {later.map((i) => (
                    <li key={i.id} className="flex items-center justify-between gap-2 py-2 text-sm">
                      <span>
                        {i.label} · vence {dateBR(i.due_date)}
                      </span>
                      <span className="tabular-nums">{brl(i.remaining)}</span>
                    </li>
                  ))}
                </ul>
                <p className="text-muted-foreground mt-2 text-xs">
                  O código de pagamento fica disponível para as próximas {VISIBLE_OPEN} parcelas.
                </p>
              </details>
            )}
          </CardContent>
        </Card>
      )}

      {c.payments.length > 0 && (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Pagamentos registrados</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <ul className="divide-y">
              {c.payments.map((p, idx) => (
                <li key={idx} className="flex flex-wrap items-baseline justify-between gap-2 py-2.5 text-sm">
                  <span className={p.reversed ? "text-muted-foreground line-through" : undefined}>
                    {dateBR(p.received_on)} · {p.installment_label} · {PAYMENT_METHOD[p.method] ?? p.method}
                    {Number(p.late_charges) > 0 && (
                      <span className="text-muted-foreground">
                        {" "}
                        · inclui {brl(p.late_charges)} de multa e juros
                      </span>
                    )}
                  </span>
                  <span className="tabular-nums">
                    {brl(p.amount)}
                    {p.reversed && <span className="ml-2 text-xs text-red-700">estornado</span>}
                  </span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {others.length > 0 && (
        <details className="rounded-xl border bg-white p-4">
          <summary className="cursor-pointer text-sm font-medium">
            Parcelas pagas e encerradas ({others.length})
          </summary>
          <ul className="mt-3 divide-y">
            {others.map((i) => (
              <li key={i.id} className="flex items-center justify-between gap-2 py-2 text-sm">
                <span>
                  {i.label} · vencimento {dateBR(i.due_date)}
                  {i.paid_at && ` · paga em ${dateBR(i.paid_at)}`}
                </span>
                <Badge variant={statusOf(i).badge}>{statusOf(i).label}</Badge>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
