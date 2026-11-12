import { ArrowLeft } from "lucide-react";
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
import { decideProposalAction } from "@/lib/actions-fase2c";
import {
  AMORTIZATION,
  brl,
  dateBR,
  dateTime,
  discountLabel,
  LOT_STATUS,
  pct,
  PROPOSAL_STATUS,
} from "@/lib/format";
import { api, ApiError, can, getMe } from "@/lib/session";
import type { Proposal } from "@/lib/types";

export const metadata: Metadata = { title: "Proposta" };

export default async function PropostaPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const me = await getMe();
  let p: Proposal;
  try {
    p = await api<Proposal>(`/proposals/${encodeURIComponent(id)}`);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 422)) notFound();
    throw e;
  }
  const st = PROPOSAL_STATUS[p.status]!;
  const open = p.status === "aberta";
  const manage = can(me, "proposals:manage");
  const canAccept = manage && can(me, "reservations:manage");

  const rows: [string, string][] = [
    ["Valor proposto", `${brl(p.total_price)} (${discountLabel(p.discount_pct)})`],
    ["Preço de tabela", brl(p.list_price)],
    ["Entrada", brl(p.down_payment)],
    [
      "Parcelas",
      p.installments_count === 0
        ? "À vista"
        : p.amortization === "sac"
          ? `${p.installments_count}× (SAC), 1ª de ${brl(p.installment_amount)}`
          : `${p.installments_count}× de ${brl(p.installment_amount)} (${AMORTIZATION[p.amortization]?.label})`,
    ],
    ["Juros", `${pct(p.interest_monthly_pct)} a.m.`],
    ["1º vencimento", dateBR(p.first_due_date)],
    ["Válida até", dateBR(p.valid_until)],
    ["Corretor", p.broker_name ?? "—"],
  ];

  return (
    <div className="space-y-6">
      <Link
        href="/app/propostas"
        className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-sm"
      >
        <ArrowLeft className="size-4" /> Propostas
      </Link>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-brand-institutional text-2xl font-bold">Proposta de {p.customer_name}</h1>
          <p className="text-muted-foreground text-sm">
            <Link href={`/app/lotes/${p.lot_id}`} className="underline">
              {p.development_name} · Quadra {p.block_code} · Lote {p.lot_number}
            </Link>{" "}
            ({LOT_STATUS[p.lot_status]?.label ?? p.lot_status}) · registrada {dateTime(p.created_at)}
          </p>
        </div>
        <Badge variant={st.badge}>{st.label}</Badge>
      </div>

      <Card>
        <CardContent>
          <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
            {rows.map(([k, v]) => (
              <div key={k}>
                <dt className="text-muted-foreground text-xs">{k}</dt>
                <dd className="font-medium">{v}</dd>
              </div>
            ))}
          </dl>
          {p.notes && <p className="text-muted-foreground mt-3 text-sm">{p.notes}</p>}
          {!open && (
            <p className="text-muted-foreground mt-3 border-t pt-3 text-sm">
              {st.label} {p.decided_at && `em ${dateTime(p.decided_at)}`}
              {p.decision_reason && ` · ${p.decision_reason}`}
              {p.reservation_id && (
                <>
                  {" · "}
                  <Link
                    href={`/app/lotes/${p.lot_id}`}
                    className="text-brand-institutional font-medium underline"
                  >
                    lote reservado — gerar contrato
                  </Link>
                </>
              )}
            </p>
          )}
        </CardContent>
      </Card>

      {open && manage && (
        <div className="grid gap-4 lg:grid-cols-3">
          {canAccept && (
            <Card className="border-brand/40">
              <CardHeader>
                <div>
                  <CardTitle>Aceitar</CardTitle>
                  <CardDescription>
                    Reserva o lote para {p.customer_name} pelo prazo escolhido.
                  </CardDescription>
                </div>
              </CardHeader>
              <CardContent>
                <ActionForm
                  action={decideProposalAction.bind(null, p.id, "accept")}
                  submitLabel="Aceitar e reservar"
                >
                  <Field label="Validade da reserva" htmlFor="hours">
                    <select id="hours" name="hours" className={selectClass} defaultValue="72">
                      <option value="24">24 horas</option>
                      <option value="48">48 horas</option>
                      <option value="72">72 horas (padrão)</option>
                      <option value="168">7 dias</option>
                    </select>
                  </Field>
                </ActionForm>
              </CardContent>
            </Card>
          )}
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Recusar</CardTitle>
                <CardDescription>O cliente ou a empresa não aceitaram as condições.</CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              <ActionForm
                action={decideProposalAction.bind(null, p.id, "reject")}
                submitLabel="Recusar"
                submitVariant="outline"
              >
                <Field label="Motivo" htmlFor="reject-reason">
                  <Input id="reject-reason" name="reason" required minLength={3} maxLength={500} />
                </Field>
              </ActionForm>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Cancelar</CardTitle>
                <CardDescription>Proposta retirada (ex.: cliente desistiu).</CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              <ActionForm
                action={decideProposalAction.bind(null, p.id, "cancel")}
                submitLabel="Cancelar proposta"
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
    </div>
  );
}
