import { ArrowLeft, History } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { TermsFields } from "@/components/contracts/terms-fields";
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
import { cancelContractAction, newVersionAction, signContractAction } from "@/lib/actions-fase2b";
import { ADJUSTMENT_INDEX, brl, CONTRACT_STATUS, dateBR, dateTime, pct } from "@/lib/format";
import { api, ApiError, can, getMe } from "@/lib/session";
import type { ContractDetail, ContractTerms } from "@/lib/types";

export const metadata: Metadata = { title: "Contrato" };

function TermsSummary({ t }: { t: ContractTerms }) {
  const rows: [string, string][] = [
    ["Valor total", brl(t.total_price)],
    ["Entrada", brl(t.down_payment)],
    [
      "Parcelas",
      t.installments_count === 0 ? "À vista" : `${t.installments_count}× de ${brl(t.installment_amount)}`,
    ],
    ["1º vencimento", dateBR(t.first_due_date)],
    ["Juros", `${pct(t.interest_monthly_pct)} a.m.`],
    ["Correção", ADJUSTMENT_INDEX[t.adjustment_index] ?? t.adjustment_index],
    ["Multa por atraso", pct(t.late_fee_pct)],
    ["Juros de mora", `${pct(t.late_interest_monthly_pct)} a.m.`],
  ];
  return (
    <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
      {rows.map(([k, v]) => (
        <div key={k}>
          <dt className="text-muted-foreground text-xs">{k}</dt>
          <dd className="font-medium tabular-nums">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export default async function ContratoPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const me = await getMe();
  let c: ContractDetail;
  try {
    c = await api<ContractDetail>(`/contracts/${encodeURIComponent(id)}`);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 422 || e.status === 403)) notFound();
    throw e;
  }
  const st = CONTRACT_STATUS[c.status]!;
  const draft = c.status === "rascunho";
  const manage = can(me, "contracts:manage") && draft;
  const sign = can(me, "contracts:sign") && draft;

  return (
    <div className="space-y-6">
      <Link
        href="/app/contratos"
        className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-sm"
      >
        <ArrowLeft className="size-4" /> Contratos
      </Link>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-brand-institutional text-2xl font-bold">Contrato nº {c.number}</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            {c.customer_name} ·{" "}
            <Link href={`/app/lotes/${c.lot_id}`} className="underline">
              {c.development_name} · Quadra {c.block_code} · Lote {c.lot_number}
            </Link>
            {c.broker_name && ` · corretor ${c.broker_name}`}
          </p>
        </div>
        <Badge variant={st.badge}>{st.label}</Badge>
      </div>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Condições (versão {c.current_version})</CardTitle>
            <CardDescription>
              {c.status === "assinado" && c.signed_at && `Assinado em ${dateTime(c.signed_at)}. `}
              {c.status === "cancelado" && `Cancelado: ${c.cancel_reason ?? ""}. `}
              {draft && "Rascunho: o lote segue reservado até a assinatura."}
            </CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          <TermsSummary t={c.terms} />
        </CardContent>
      </Card>

      {(sign || manage) && (
        <div className="grid gap-4 lg:grid-cols-2">
          {sign && (
            <Card>
              <CardHeader>
                <div>
                  <CardTitle>Registrar assinatura</CardTitle>
                  <CardDescription>
                    Confirme que o comprador assinou a versão {c.current_version}. O lote passa a vendido e as
                    condições ficam congeladas.
                  </CardDescription>
                </div>
              </CardHeader>
              <CardContent>
                <ActionForm
                  action={signContractAction.bind(null, c.id, c.current_version)}
                  submitLabel="Registrar assinatura"
                  submitVariant="institutional"
                >
                  {null}
                </ActionForm>
              </CardContent>
            </Card>
          )}
          {manage && (
            <Card>
              <CardHeader>
                <div>
                  <CardTitle>Cancelar rascunho</CardTitle>
                  <CardDescription>O lote volta a ficar disponível.</CardDescription>
                </div>
              </CardHeader>
              <CardContent>
                <ActionForm
                  action={cancelContractAction.bind(null, c.id)}
                  submitLabel="Cancelar contrato"
                  submitVariant="outline"
                >
                  <Field label="Motivo" htmlFor="cancel-reason">
                    <Input id="cancel-reason" name="reason" required minLength={3} maxLength={500} />
                  </Field>
                </ActionForm>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {manage && (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Alterar condições</CardTitle>
              <CardDescription>
                Cria a versão {c.current_version + 1}; as anteriores ficam no histórico.
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <ActionForm
              action={newVersionAction.bind(null, c.id, c.current_version)}
              submitLabel="Salvar nova versão"
            >
              <TermsFields initial={c.terms} totalHint={brl(c.terms.total_price)} />
              <Field label="Motivo da alteração" htmlFor="version-reason">
                <Input id="version-reason" name="reason" required minLength={3} maxLength={500} />
              </Field>
            </ActionForm>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <div>
            <CardTitle className="flex items-center gap-2">
              <History className="text-brand size-4" /> Versões
            </CardTitle>
          </div>
        </CardHeader>
        <CardContent>
          <ol className="divide-y">
            {c.versions.map((v) => (
              <li
                key={v.version}
                className="flex flex-wrap items-baseline justify-between gap-2 py-2.5 text-sm"
              >
                <span>
                  <strong>v{v.version}</strong> · {brl(v.total_price)} · entrada {brl(v.down_payment)} ·{" "}
                  {v.installments_count === 0
                    ? "à vista"
                    : `${v.installments_count}× ${brl(v.installment_amount)}`}
                  {v.reason && <span className="text-muted-foreground"> · {v.reason}</span>}
                </span>
                <span className="text-muted-foreground text-xs">{dateTime(v.created_at)}</span>
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>
    </div>
  );
}
