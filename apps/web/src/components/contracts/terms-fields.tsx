import { Field, selectClass } from "@/components/ui/action-form";
import { Input } from "@/components/ui/primitives";
import { ADJUSTMENT_INDEX } from "@/lib/format";
import type { ContractTerms } from "@/lib/types";

const br = (v: string | null | undefined) => (v == null ? "" : v.replace(".", ","));

function defaultDue(): string {
  const d = new Date();
  d.setMonth(d.getMonth() + 1);
  return d.toISOString().slice(0, 10);
}

/** Campos das condições do contrato (entrada, parcelas, juros, correção, multa). */
export function TermsFields({ initial, totalHint }: { initial?: ContractTerms; totalHint: string }) {
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
        <Field label="Entrada (R$)" htmlFor="down_payment">
          <Input
            id="down_payment"
            name="down_payment"
            inputMode="decimal"
            defaultValue={br(initial?.down_payment)}
            required
          />
        </Field>
        <Field label="Nº de parcelas (0 = à vista)" htmlFor="installments_count">
          <Input
            id="installments_count"
            name="installments_count"
            type="number"
            min={0}
            max={480}
            defaultValue={initial?.installments_count ?? 120}
            required
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
        <Field label="Juros (% a.m.)" htmlFor="interest_monthly_pct">
          <Input
            id="interest_monthly_pct"
            name="interest_monthly_pct"
            inputMode="decimal"
            defaultValue={br(initial?.interest_monthly_pct ?? "0")}
          />
        </Field>
        <Field label="Correção" htmlFor="adjustment_index">
          <select
            id="adjustment_index"
            name="adjustment_index"
            className={selectClass}
            defaultValue={initial?.adjustment_index ?? "nenhum"}
          >
            {Object.entries(ADJUSTMENT_INDEX).map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Multa por atraso (%)" htmlFor="late_fee_pct">
          <Input
            id="late_fee_pct"
            name="late_fee_pct"
            inputMode="decimal"
            defaultValue={br(initial?.late_fee_pct ?? "2.00")}
          />
        </Field>
        <Field label="Juros de mora (% a.m.)" htmlFor="late_interest_monthly_pct">
          <Input
            id="late_interest_monthly_pct"
            name="late_interest_monthly_pct"
            inputMode="decimal"
            defaultValue={br(initial?.late_interest_monthly_pct ?? "1.00")}
          />
        </Field>
      </div>
      <Field label="Observações" htmlFor="notes">
        <Input id="notes" name="notes" defaultValue={initial?.notes ?? ""} maxLength={4000} />
      </Field>
      <p className="text-muted-foreground text-xs">
        Juros, multa e correção são configuráveis e devem ser validados por profissional habilitado. A parcela
        é calculada pelo servidor (Tabela Price quando há juros).
      </p>
    </div>
  );
}
