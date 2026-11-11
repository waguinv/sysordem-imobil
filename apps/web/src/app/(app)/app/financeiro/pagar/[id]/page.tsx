import { ArrowLeft, History } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
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
import { payableAction } from "@/lib/actions-fase3b";
import {
  brl,
  dateBR,
  dateTime,
  PAYABLE_CATEGORY,
  PAYABLE_EVENT,
  PAYMENT_METHOD,
  payableStatus,
} from "@/lib/format";
import { api, ApiError, can, getMe } from "@/lib/session";
import type { PayableDetail } from "@/lib/types";

export const metadata: Metadata = { title: "Conta a pagar" };

export default async function ContaPagarPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const me = await getMe();
  let p: PayableDetail;
  try {
    p = await api<PayableDetail>(`/payables/${encodeURIComponent(id)}`);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 422 || e.status === 403)) notFound();
    throw e;
  }
  const st = payableStatus(p);
  const manage = can(me, "finance:manage");
  const fromCredits = p.participations_count > 0;
  const today = new Date().toLocaleDateString("sv-SE", { timeZone: "America/Sao_Paulo" });

  const rows: [string, string][] = [
    ["Categoria", PAYABLE_CATEGORY[p.category] ?? p.category],
    ["Valor", brl(p.amount)],
    ["Vencimento", dateBR(p.due_date)],
    ["Empreendimento", p.development_name ?? "—"],
    ["Pagamento", p.paid_on ? `${dateBR(p.paid_on)} · ${PAYMENT_METHOD[p.method ?? ""] ?? p.method}` : "—"],
  ];

  return (
    <div className="space-y-6">
      <Link
        href="/app/financeiro/pagar"
        className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-sm"
      >
        <ArrowLeft className="size-4" /> Contas a pagar
      </Link>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-brand-institutional text-2xl font-bold">{p.payee_name}</h1>
          <p className="text-muted-foreground text-sm">{p.description}</p>
        </div>
        <Badge variant={st.badge}>{st.label}</Badge>
      </div>

      <Card>
        <CardContent>
          <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-3 lg:grid-cols-5">
            {rows.map(([k, v]) => (
              <div key={k}>
                <dt className="text-muted-foreground text-xs">{k}</dt>
                <dd className="font-medium tabular-nums">{v}</dd>
              </div>
            ))}
          </dl>
          {fromCredits && (
            <p className="text-muted-foreground mt-3 text-xs">
              Gerada a partir de {p.participations_count} lançamento(s) de{" "}
              <Link href="/app/comissoes" className="underline">
                comissões e repasses
              </Link>
              . Cancelar a conta devolve esses lançamentos para “a acertar”.
            </p>
          )}
          {p.notes && <p className="text-muted-foreground mt-2 text-sm">{p.notes}</p>}
        </CardContent>
      </Card>

      {manage && p.status === "aberta" && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card className="border-brand/40">
            <CardHeader>
              <div>
                <CardTitle>Registrar pagamento</CardTitle>
              </div>
            </CardHeader>
            <CardContent>
              <ActionForm action={payableAction.bind(null, p.id, "pay")} submitLabel="Registrar pagamento">
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Data" htmlFor="paid_on">
                    <Input id="paid_on" name="paid_on" type="date" defaultValue={today} max={today} />
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
                </div>
                <Field label="Observação" htmlFor="pay-notes">
                  <Input id="pay-notes" name="notes" maxLength={500} placeholder="ex.: comprovante 1234" />
                </Field>
              </ActionForm>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Cancelar conta</CardTitle>
                <CardDescription>A conta fica no histórico como cancelada.</CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              <ActionForm
                action={payableAction.bind(null, p.id, "cancel")}
                submitLabel="Cancelar conta"
                submitVariant="outline"
              >
                <Field label="Motivo" htmlFor="cancel-reason">
                  <Input id="cancel-reason" name="reason" required minLength={3} maxLength={500} />
                </Field>
              </ActionForm>
            </CardContent>
          </Card>
        </div>
      )}

      {can(me, "finance:reverse") && p.status === "paga" && (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Estornar pagamento</CardTitle>
              <CardDescription>
                A conta volta a ficar em aberto; o pagamento continua no histórico.
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <ActionForm
              action={payableAction.bind(null, p.id, "reverse")}
              submitLabel="Estornar"
              submitVariant="outline"
              className="sm:max-w-sm"
            >
              <Field label="Motivo" htmlFor="rev-reason">
                <Input id="rev-reason" name="reason" required minLength={3} maxLength={500} />
              </Field>
            </ActionForm>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <div>
            <CardTitle className="flex items-center gap-2">
              <History className="text-brand size-4" /> Histórico
            </CardTitle>
          </div>
        </CardHeader>
        <CardContent>
          <ol className="divide-y">
            {p.events.map((e, i) => (
              <li key={i} className="flex flex-wrap items-baseline justify-between gap-2 py-2.5 text-sm">
                <span>
                  <strong>{PAYABLE_EVENT[e.kind] ?? e.kind}</strong>
                  {e.event_date && ` · ${dateBR(e.event_date)}`}
                  {e.method && ` · ${PAYMENT_METHOD[e.method] ?? e.method}`}
                  {e.reason && <span className="text-muted-foreground"> · {e.reason}</span>}
                </span>
                <span className="text-muted-foreground text-xs">
                  {e.author_name ?? "—"} · {dateTime(e.created_at)}
                </span>
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>
    </div>
  );
}
