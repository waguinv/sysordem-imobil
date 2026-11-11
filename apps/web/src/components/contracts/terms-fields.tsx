import { Field, selectClass } from "@/components/ui/action-form";
import { Input } from "@/components/ui/primitives";
import { ADJUSTMENT_INDEX, AMORTIZATION, pct } from "@/lib/format";
import type { ContractPolicy, ContractTerms } from "@/lib/types";

const br = (v: string | null | undefined) => (v == null ? "" : v.replace(".", ","));

function defaultDue(): string {
  const d = new Date();
  d.setMonth(d.getMonth() + 1);
  return d.toISOString().slice(0, 10);
}

/**
 * Condições do contrato. Os padrões e limites vêm da política da empresa; dentro deles, empresa e
 * cliente escolhem entrada, parcelas, juros e o sistema (Price ou SAC).
 */
export function TermsFields({
  initial,
  totalHint,
  policy,
  compact = false,
}: {
  initial?: Partial<ContractTerms>;
  totalHint: string;
  policy: ContractPolicy;
  /** proposta: sem correção nem encargos de atraso (definidos no contrato) */
  compact?: boolean;
}) {
  const overrideLate = policy.allow_override_late_charges;
  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Valor total (R$)" htmlFor="total_price">
          <Input
            id="total_price"
            name="total_price"
            inputMode="decimal"
            defaultValue={br(initial?.total_price)}
            placeholder={totalHint}
          />
        </Field>
        <Field
          label={`Entrada (R$)${Number(policy.min_down_payment_pct) > 0 ? ` — mín. ${pct(policy.min_down_payment_pct)}` : ""}`}
          htmlFor="down_payment"
        >
          <Input
            id="down_payment"
            name="down_payment"
            inputMode="decimal"
            defaultValue={br(initial?.down_payment)}
            required
          />
        </Field>
        <Field
          label={`Nº de parcelas (0 = à vista; máx. ${policy.max_installments})`}
          htmlFor="installments_count"
        >
          <Input
            id="installments_count"
            name="installments_count"
            type="number"
            min={0}
            max={policy.max_installments}
            defaultValue={initial?.installments_count ?? Math.min(120, policy.max_installments)}
            required
          />
        </Field>
        <Field label="Sistema de amortização" htmlFor="amortization">
          <select
            id="amortization"
            name="amortization"
            className={selectClass}
            defaultValue={initial?.amortization ?? policy.default_amortization}
          >
            {policy.allowed_amortizations.map((a) => (
              <option key={a} value={a}>
                {AMORTIZATION[a]?.label} ({AMORTIZATION[a]?.hint})
              </option>
            ))}
          </select>
        </Field>
        <Field
          label={`Juros (% a.m.; máx. ${pct(policy.max_interest_monthly_pct)})`}
          htmlFor="interest_monthly_pct"
        >
          <Input
            id="interest_monthly_pct"
            name="interest_monthly_pct"
            inputMode="decimal"
            defaultValue={br(initial?.interest_monthly_pct ?? policy.default_interest_monthly_pct)}
          />
        </Field>
        <Field label="1º vencimento" htmlFor="first_due_date">
          <Input
            id="first_due_date"
            name="first_due_date"
            type="date"
            defaultValue={initial?.first_due_date ?? defaultDue()}
          />
        </Field>
        {!compact && (
          <Field label="Correção" htmlFor="adjustment_index">
            <select
              id="adjustment_index"
              name="adjustment_index"
              className={selectClass}
              defaultValue={initial?.adjustment_index ?? policy.default_adjustment_index}
            >
              {Object.entries(ADJUSTMENT_INDEX).map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </Field>
        )}
        {compact ? null : overrideLate ? (
          <>
            <Field label="Multa por atraso (%)" htmlFor="late_fee_pct">
              <Input
                id="late_fee_pct"
                name="late_fee_pct"
                inputMode="decimal"
                defaultValue={br(initial?.late_fee_pct ?? policy.late_fee_pct)}
              />
            </Field>
            <Field label="Juros de mora (% a.m.)" htmlFor="late_interest_monthly_pct">
              <Input
                id="late_interest_monthly_pct"
                name="late_interest_monthly_pct"
                inputMode="decimal"
                defaultValue={br(initial?.late_interest_monthly_pct ?? policy.late_interest_monthly_pct)}
              />
            </Field>
          </>
        ) : (
          <div className="text-muted-foreground self-end text-xs sm:col-span-2">
            Multa {pct(policy.late_fee_pct)} e juros de mora {pct(policy.late_interest_monthly_pct)} a.m. —
            definidos pela política da empresa.
          </div>
        )}
      </div>
      <Field label="Observações" htmlFor="notes">
        <Input id="notes" name="notes" defaultValue={initial?.notes ?? ""} maxLength={4000} />
      </Field>
      <p className="text-muted-foreground text-xs">
        A 1ª parcela é calculada pelo servidor (Price: parcelas iguais; SAC: a 1ª é a maior e as demais
        diminuem). Juros, multa e correção devem ser validados por profissional habilitado.
      </p>
    </div>
  );
}
