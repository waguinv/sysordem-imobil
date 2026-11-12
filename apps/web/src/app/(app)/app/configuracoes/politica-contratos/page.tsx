import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { ActionForm, Field, selectClass } from "@/components/ui/action-form";
import { Badge, Card, CardContent, CardDescription, CardTitle, Input } from "@/components/ui/primitives";
import { savePolicyAction } from "@/lib/actions-fase2b";
import { ADJUSTMENT_INDEX, AMORTIZATION } from "@/lib/format";
import { api, can, getMe } from "@/lib/session";
import type { ContractPolicy } from "@/lib/types";

export const metadata: Metadata = { title: "Política de contratos" };

const br = (v: string) => v.replace(".", ",");

export default async function PoliticaContratosPage() {
  const me = await getMe();
  if (!can(me, "settings:manage")) redirect("/app");
  const p = await api<ContractPolicy>("/settings/contract-policy");
  return (
    <div className="max-w-3xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-brand-institutional text-2xl font-bold">Política de contratos</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Padrões e limites de {me.tenant.name}. Em cada contrato, empresa e cliente escolhem as condições
            dentro destes limites.
          </p>
        </div>
        {p.is_default && <Badge variant="warning">Usando o padrão do sistema</Badge>}
      </div>
      <Card>
        <CardContent>
          <ActionForm action={savePolicyAction} submitLabel="Salvar política">
            <section className="space-y-3">
              <CardTitle className="text-base">Financiamento</CardTitle>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Juros padrão (% a.m.)" htmlFor="default_interest_monthly_pct">
                  <Input
                    id="default_interest_monthly_pct"
                    name="default_interest_monthly_pct"
                    inputMode="decimal"
                    defaultValue={br(p.default_interest_monthly_pct)}
                    required
                  />
                </Field>
                <Field label="Juros máximo (% a.m.)" htmlFor="max_interest_monthly_pct">
                  <Input
                    id="max_interest_monthly_pct"
                    name="max_interest_monthly_pct"
                    inputMode="decimal"
                    defaultValue={br(p.max_interest_monthly_pct)}
                    required
                  />
                </Field>
                <fieldset className="space-y-1.5">
                  <legend className="text-sm font-medium">Sistemas permitidos</legend>
                  <div className="flex gap-4 pt-1">
                    {(["price", "sac"] as const).map((a) => (
                      <label key={a} className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          name="allowed_amortizations"
                          value={a}
                          defaultChecked={p.allowed_amortizations.includes(a)}
                          className="accent-brand size-4"
                        />
                        {AMORTIZATION[a]!.label}{" "}
                        <span className="text-muted-foreground">({AMORTIZATION[a]!.hint})</span>
                      </label>
                    ))}
                  </div>
                </fieldset>
                <Field label="Sistema padrão" htmlFor="default_amortization">
                  <select
                    id="default_amortization"
                    name="default_amortization"
                    className={selectClass}
                    defaultValue={p.default_amortization}
                  >
                    {(["price", "sac"] as const).map((a) => (
                      <option key={a} value={a}>
                        {AMORTIZATION[a]!.label}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Nº máximo de parcelas" htmlFor="max_installments">
                  <Input
                    id="max_installments"
                    name="max_installments"
                    type="number"
                    min={0}
                    max={480}
                    defaultValue={p.max_installments}
                    required
                  />
                </Field>
                <Field label="Entrada mínima (% do total)" htmlFor="min_down_payment_pct">
                  <Input
                    id="min_down_payment_pct"
                    name="min_down_payment_pct"
                    inputMode="decimal"
                    defaultValue={br(p.min_down_payment_pct)}
                    required
                  />
                </Field>
                <Field label="Correção padrão" htmlFor="default_adjustment_index">
                  <select
                    id="default_adjustment_index"
                    name="default_adjustment_index"
                    className={selectClass}
                    defaultValue={p.default_adjustment_index}
                  >
                    {Object.entries(ADJUSTMENT_INDEX).map(([v, l]) => (
                      <option key={v} value={v}>
                        {l}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>
            </section>

            <section className="space-y-3 border-t pt-4">
              <CardTitle className="text-base">Atraso</CardTitle>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Multa por atraso (%)" htmlFor="late_fee_pct">
                  <Input
                    id="late_fee_pct"
                    name="late_fee_pct"
                    inputMode="decimal"
                    defaultValue={br(p.late_fee_pct)}
                    required
                  />
                </Field>
                <Field label="Juros de mora (% a.m.)" htmlFor="late_interest_monthly_pct">
                  <Input
                    id="late_interest_monthly_pct"
                    name="late_interest_monthly_pct"
                    inputMode="decimal"
                    defaultValue={br(p.late_interest_monthly_pct)}
                    required
                  />
                </Field>
              </div>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  name="allow_override_late_charges"
                  defaultChecked={p.allow_override_late_charges}
                  className="accent-brand size-4"
                />
                Permitir negociar multa e juros de mora em cada contrato
              </label>
            </section>

            <section className="space-y-3 border-t pt-4">
              <Field label="Motivo da alteração" htmlFor="reason">
                <Input id="reason" name="reason" required minLength={3} maxLength={500} />
              </Field>
              <CardDescription>
                A alteração vale para novos contratos e novas versões; contratos já gerados mantêm suas
                condições. Valores devem ser validados por profissional jurídico/contábil.
              </CardDescription>
            </section>
          </ActionForm>
        </CardContent>
      </Card>
    </div>
  );
}
