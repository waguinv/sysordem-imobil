import { HandCoins } from "lucide-react";
import Link from "next/link";
import { TermsFields } from "@/components/contracts/terms-fields";
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
import { createProposalAction } from "@/lib/actions-fase2c";
import { brl, dateBR, discountLabel, PROPOSAL_STATUS } from "@/lib/format";
import { api, can, type Me } from "@/lib/session";
import type { Broker, ContractPolicy, Customer, LotDetail, Proposal } from "@/lib/types";

function inDays(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

/** Propostas do lote: as abertas (negociação em andamento) e o formulário de nova proposta. */
export async function ProposalPanel({ lot, me }: { lot: LotDetail; me: Me }) {
  if (!can(me, "proposals:view")) return null;
  const forSale = lot.status === "disponivel" || lot.status === "reservado";
  const manage = can(me, "proposals:manage") && forSale;
  const [open, policy, customers, brokers] = await Promise.all([
    api<Proposal[]>(`/proposals?lot_id=${lot.id}&status=aberta`),
    manage ? api<ContractPolicy>("/settings/contract-policy") : Promise.resolve(null),
    manage ? api<Customer[]>("/customers?limit=500") : Promise.resolve([] as Customer[]),
    manage && can(me, "brokers:view")
      ? api<Broker[]>("/brokers?active=true")
      : Promise.resolve([] as Broker[]),
  ]);
  if (open.length === 0 && !policy) return null;

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <HandCoins className="text-brand size-4" /> Propostas
          </CardTitle>
          <CardDescription>
            Proposta aberta não segura o lote. Ao aceitar, o lote é reservado para o cliente.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {open.length > 0 && (
          <ul className="divide-y">
            {open.map((p) => (
              <li key={p.id}>
                <Link
                  href={`/app/propostas/${p.id}`}
                  className="hover:bg-accent/60 -mx-2 flex flex-wrap items-center justify-between gap-2 rounded-lg px-2 py-2.5 text-sm"
                >
                  <span>
                    <strong className="font-medium">{p.customer_name}</strong> · {brl(p.total_price)}
                    <span className="text-muted-foreground">
                      {" "}
                      ({discountLabel(p.discount_pct)}) · válida até {dateBR(p.valid_until)}
                    </span>
                  </span>
                  <Badge variant={PROPOSAL_STATUS[p.status]!.badge}>{PROPOSAL_STATUS[p.status]!.label}</Badge>
                </Link>
              </li>
            ))}
          </ul>
        )}
        {policy &&
          (customers.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              Para propor, cadastre o comprador em{" "}
              <Link href="/app/compradores" className="text-brand-institutional font-medium underline">
                Compradores
              </Link>{" "}
              ou converta um lead.
            </p>
          ) : (
            <details className="rounded-lg border p-3" open={open.length === 0}>
              <summary className="text-brand-institutional cursor-pointer text-sm font-medium">
                Nova proposta
              </summary>
              <ActionForm
                action={createProposalAction.bind(null, lot.id)}
                submitLabel="Registrar proposta"
                className="mt-3"
              >
                <div className="grid gap-3 sm:grid-cols-3">
                  <Field label="Comprador" htmlFor="p-customer_id">
                    <select id="p-customer_id" name="customer_id" className={selectClass} required>
                      {customers.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                  {brokers.length > 0 && (
                    <Field label="Corretor" htmlFor="p-broker_id">
                      <select id="p-broker_id" name="broker_id" className={selectClass} defaultValue="">
                        <option value="">— sem corretor —</option>
                        {brokers.map((b) => (
                          <option key={b.id} value={b.id}>
                            {b.name}
                          </option>
                        ))}
                      </select>
                    </Field>
                  )}
                  <Field label="Válida até" htmlFor="valid_until">
                    <Input id="valid_until" name="valid_until" type="date" defaultValue={inDays(7)} />
                  </Field>
                </div>
                <TermsFields totalHint={brl(lot.price)} policy={policy} compact />
              </ActionForm>
            </details>
          ))}
      </CardContent>
    </Card>
  );
}
