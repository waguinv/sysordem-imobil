import { CalendarClock } from "lucide-react";
import Link from "next/link";
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
import { TermsFields } from "@/components/contracts/terms-fields";
import { closeReservationAction, createReservationAction } from "@/lib/actions-fase2a";
import { createContractAction } from "@/lib/actions-fase2b";
import { brl, dateTime, RESERVATION_STATUS, timeLeft } from "@/lib/format";
import { api, ApiError, can, type Me } from "@/lib/session";
import type { Broker, ContractPolicy, Customer, LotDetail, Proposal, Reservation } from "@/lib/types";

/** Reserva do lote: mostra a reserva ativa (cancelar / gerar contrato) ou o formulário para reservar. */
export async function ReservationPanel({ lot, me }: { lot: LotDetail; me: Me }) {
  const manage = can(me, "reservations:manage");

  if (lot.active_reservation_id && can(me, "reservations:view")) {
    let r: Reservation;
    try {
      r = await api<Reservation>(`/reservations/${lot.active_reservation_id}`);
    } catch (e) {
      // Corretor só vê as próprias reservas: a de outro corretor aparece apenas como "lote reservado".
      if (e instanceof ApiError && e.status === 404) {
        return (
          <Card className="border-lot-reservado/60">
            <CardContent>
              <p className="text-muted-foreground flex items-center gap-2 text-sm">
                <CalendarClock className="size-4 text-amber-600" /> Lote reservado para outro cliente.
              </p>
            </CardContent>
          </Card>
        );
      }
      throw e;
    }
    const [policy, accepted] = await Promise.all([
      can(me, "contracts:manage") ? api<ContractPolicy>("/settings/contract-policy") : Promise.resolve(null),
      can(me, "proposals:view")
        ? api<Proposal[]>(`/proposals?reservation_id=${r.id}`)
        : Promise.resolve([] as Proposal[]),
    ]);
    const proposal = accepted[0];
    const st = RESERVATION_STATUS[r.status]!;
    return (
      <>
        <Card className="border-lot-reservado/60">
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-2">
                <CalendarClock className="size-4 text-amber-600" /> Reserva ativa
              </CardTitle>
              <CardDescription>
                Vence {timeLeft(r.expires_at)} ({dateTime(r.expires_at)}). Ao vencer, o lote volta a ficar
                disponível.
              </CardDescription>
            </div>
            <Badge variant={st.badge}>{st.label}</Badge>
          </CardHeader>
          <CardContent className="space-y-4">
            <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
              <div>
                <dt className="text-muted-foreground text-xs">Comprador</dt>
                <dd className="font-medium">{r.customer_name}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground text-xs">Corretor</dt>
                <dd className="font-medium">{r.broker_name ?? "—"}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground text-xs">Preço reservado</dt>
                <dd className="font-medium">{brl(r.price)}</dd>
              </div>
            </dl>
            {manage && (
              <div className="border-t pt-4">
                <ActionForm
                  action={closeReservationAction.bind(null, r.id, lot.id, "cancel")}
                  submitLabel="Cancelar reserva"
                  submitVariant="outline"
                  className="sm:max-w-sm"
                >
                  <Field label="Motivo do cancelamento" htmlFor="cancel-reason">
                    <Input
                      id="cancel-reason"
                      name="reason"
                      placeholder="ex.: desistência"
                      required
                      minLength={3}
                    />
                  </Field>
                </ActionForm>
              </div>
            )}
          </CardContent>
        </Card>
        {policy && (
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Gerar contrato</CardTitle>
                <CardDescription>
                  Cria o contrato em rascunho com estas condições. A reserva é encerrada e o lote fica preso
                  ao contrato até a assinatura.
                  {proposal && (
                    <>
                      {" "}
                      Condições preenchidas com a{" "}
                      <Link href={`/app/propostas/${proposal.id}`} className="underline">
                        proposta aceita
                      </Link>
                      .
                    </>
                  )}
                </CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              <ActionForm action={createContractAction.bind(null, r.id, lot.id)} submitLabel="Gerar contrato">
                <TermsFields
                  totalHint={brl(r.price)}
                  policy={policy}
                  initial={
                    proposal && {
                      total_price: proposal.total_price,
                      down_payment: proposal.down_payment,
                      installments_count: proposal.installments_count,
                      amortization: proposal.amortization,
                      interest_monthly_pct: proposal.interest_monthly_pct,
                      first_due_date: proposal.first_due_date,
                      notes: proposal.notes,
                    }
                  }
                />
              </ActionForm>
            </CardContent>
          </Card>
        )}
      </>
    );
  }

  if (lot.status !== "disponivel" || !manage) return null;

  const [customers, brokers] = await Promise.all([
    api<Customer[]>("/customers?limit=500"),
    can(me, "brokers:view") ? api<Broker[]>("/brokers?active=true") : Promise.resolve([] as Broker[]),
  ]);

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Reservar lote</CardTitle>
          <CardDescription>O preço atual ({brl(lot.price)}) fica registrado na reserva.</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        {customers.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            Cadastre um comprador em{" "}
            <Link href="/app/compradores" className="text-brand-institutional font-medium underline">
              Compradores
            </Link>{" "}
            para reservar.
          </p>
        ) : (
          <ActionForm
            action={createReservationAction.bind(null, lot.id)}
            submitLabel="Reservar"
            className="grid gap-3 sm:grid-cols-3 [&>button]:sm:col-span-3 [&>button]:sm:w-fit [&>p]:sm:col-span-3"
          >
            <Field label="Comprador" htmlFor="customer_id">
              <select id="customer_id" name="customer_id" className={selectClass} required>
                {customers.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Corretor" htmlFor="broker_id">
              <select id="broker_id" name="broker_id" className={selectClass} defaultValue="">
                <option value="">— sem corretor —</option>
                {brokers.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Validade" htmlFor="hours">
              <select id="hours" name="hours" className={selectClass} defaultValue="72">
                <option value="24">24 horas</option>
                <option value="48">48 horas</option>
                <option value="72">72 horas (padrão)</option>
                <option value="168">7 dias</option>
              </select>
            </Field>
          </ActionForm>
        )}
      </CardContent>
    </Card>
  );
}
