import { ArrowLeft, Webhook } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ChargeActions, ChargeCode } from "@/components/finance/charge-panel";
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/primitives";
import {
  brl,
  CHARGE_METHOD,
  CHARGE_STATUS,
  dateBR,
  dateTime,
  installmentLabel,
  WEBHOOK_EVENT,
  WEBHOOK_RESULT,
} from "@/lib/format";
import { api, ApiError, can, getMe } from "@/lib/session";
import type { ChargeDetail, PaymentProviderInfo } from "@/lib/types";

export const metadata: Metadata = { title: "Cobrança" };

export default async function CobrancaPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const me = await getMe();
  let c: ChargeDetail;
  try {
    c = await api<ChargeDetail>(`/charges/${encodeURIComponent(id)}`);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 422 || e.status === 403)) notFound();
    throw e;
  }
  const providers = await api<PaymentProviderInfo[]>("/payment-providers");
  const st = CHARGE_STATUS[c.status];

  const rows: [string, string][] = [
    ["Meio", CHARGE_METHOD[c.method] ?? c.method],
    ["Valor da cobrança", brl(c.amount)],
    ["Vencimento", dateBR(c.due_date)],
    ["Valor pago", brl(c.paid_amount)],
    ["Data do pagamento", dateBR(c.paid_on)],
    ["Provedor", c.provider_label],
  ];

  return (
    <div className="space-y-6">
      <Link
        href={`/app/financeiro/parcelas/${c.installment_id}`}
        className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-sm"
      >
        <ArrowLeft className="size-4" /> {installmentLabel(c.installment_number)} · Contrato{" "}
        {c.contract_number}
      </Link>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-brand-institutional text-2xl font-bold">
            Cobrança {CHARGE_METHOD[c.method]} · {c.customer_name}
          </h1>
          <p className="text-muted-foreground text-sm">
            Emitida {dateTime(c.created_at)}
            {c.created_by_name && ` por ${c.created_by_name}`} · id no PSP{" "}
            <code className="text-xs">{c.external_id}</code>
          </p>
        </div>
        <Badge variant={st?.badge ?? "muted"}>{st?.label ?? c.status}</Badge>
      </div>

      {c.status_reason && (
        <p
          className={
            c.status === "divergente"
              ? "rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm whitespace-pre-line text-red-800"
              : "text-muted-foreground text-sm whitespace-pre-line"
          }
        >
          {c.status_reason}
        </p>
      )}

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

      {(c.status === "pendente" || c.status === "divergente") && (
        <Card>
          <CardContent className="space-y-4">
            {c.status === "pendente" && <ChargeCode charge={c} />}
            {c.status === "divergente" && (
              <p className="text-sm">
                O PSP confirmou o pagamento, mas ele não pôde ser lançado na parcela. Devolva ao pagador ou
                registre o valor manualmente e marque como resolvida.
              </p>
            )}
            <ChargeActions
              charge={c}
              canManage={can(me, "finance:manage")}
              canResolve={can(me, "finance:reverse")}
              sandbox={providers.some((p) => p.sandbox)}
            />
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <div>
            <CardTitle className="flex items-center gap-2">
              <Webhook className="text-brand size-4" /> Notificações do PSP
            </CardTitle>
            <CardDescription>
              Webhooks autenticados por assinatura. Reenvios do mesmo evento são descartados.
            </CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          {c.events.length === 0 ? (
            <p className="text-muted-foreground text-sm">Nenhuma notificação recebida.</p>
          ) : (
            <ul className="divide-y text-sm">
              {c.events.map((e) => (
                <li key={e.id} className="flex flex-wrap items-baseline justify-between gap-2 py-2">
                  <span>
                    <strong>{WEBHOOK_EVENT[e.event_type] ?? e.event_type}</strong> ·{" "}
                    {WEBHOOK_RESULT[e.result] ?? e.result}
                    {e.detail && <span className="text-muted-foreground"> — {e.detail}</span>}
                  </span>
                  <span className="text-muted-foreground text-xs">
                    {dateTime(e.received_at)} · <code>{e.event_id}</code>
                  </span>
                </li>
              ))}
            </ul>
          )}
          {c.payment_id && (
            <p className="text-muted-foreground mt-3 text-xs">
              Lançado como recebimento na{" "}
              <Link
                href={`/app/financeiro/parcelas/${c.installment_id}`}
                className="text-brand-institutional underline"
              >
                parcela
              </Link>
              .
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
