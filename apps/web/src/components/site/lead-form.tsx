import { ActionForm, Field, selectClass } from "@/components/ui/action-form";
import { Input } from "@/components/ui/primitives";
import { submitLeadAction } from "@/lib/actions-fase5a";

type LotOption = { id: string; label: string };

/** Formulário público de contato. O campo "website" é isca para robôs (fica fora da tela). */
export function LeadForm({
  slug,
  developmentId,
  lots = [],
  siteName,
}: {
  slug: string;
  developmentId?: string;
  lots?: LotOption[];
  siteName: string;
}) {
  return (
    <ActionForm action={submitLeadAction.bind(null, slug)} submitLabel="Quero ser contatado" resetOnSuccess>
      {developmentId && <input type="hidden" name="development_id" value={developmentId} />}
      <div aria-hidden="true" className="absolute -left-[9999px] h-0 w-0 overflow-hidden">
        <label htmlFor="website">Não preencha</label>
        <input id="website" name="website" tabIndex={-1} autoComplete="off" />
      </div>
      <Field label="Nome" htmlFor="lead-name">
        <Input id="lead-name" name="name" required minLength={2} maxLength={160} autoComplete="name" />
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Telefone / WhatsApp" htmlFor="lead-phone">
          <Input id="lead-phone" name="phone" maxLength={30} inputMode="tel" autoComplete="tel" />
        </Field>
        <Field label="E-mail" htmlFor="lead-email">
          <Input id="lead-email" name="email" type="email" maxLength={254} autoComplete="email" />
        </Field>
      </div>
      {lots.length > 0 && (
        <Field label="Lote de interesse (opcional)" htmlFor="lead-lot">
          <select id="lead-lot" name="lot_id" className={selectClass} defaultValue="">
            <option value="">— qualquer —</option>
            {lots.map((l) => (
              <option key={l.id} value={l.id}>
                {l.label}
              </option>
            ))}
          </select>
        </Field>
      )}
      <Field label="Mensagem (opcional)" htmlFor="lead-message">
        <textarea
          id="lead-message"
          name="message"
          rows={3}
          maxLength={2000}
          className={`${selectClass} h-auto py-2`}
        />
      </Field>
      <label className="flex items-start gap-2 text-xs">
        <input type="checkbox" name="consent" required className="mt-0.5 size-4" />
        <span>
          Autorizo {siteName} a usar meus dados de contato para falar comigo sobre lotes e condições de compra
          (LGPD).
        </span>
      </label>
    </ActionForm>
  );
}
