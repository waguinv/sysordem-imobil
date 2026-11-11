import { FlaskConical, QrCode } from "lucide-react";
import Link from "next/link";
import { ActionForm, Field, selectClass } from "@/components/ui/action-form";
import { CopyButton } from "@/components/ui/copy-button";
import {
  Badge,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Input,
} from "@/components/ui/primitives";
import {
  cancelChargeAction,
  issueChargeAction,
  resolveChargeAction,
  simulateChargeAction,
} from "@/lib/actions-fase4a";
import { brl, CHARGE_METHOD, CHARGE_STATUS, dateBR, dateTime } from "@/lib/format";
import type { Charge, PaymentProviderInfo } from "@/lib/types";

/** Código Pix / linha digitável com botão de copiar. */
export function ChargeCode({ charge }: { charge: Charge }) {
  const code = charge.pix_code ?? charge.boleto_line;
  if (!code) return null;
  return (
    <div className="space-y-2">
      <p className="text-muted-foreground text-xs">
        {charge.method === "pix" ? "Pix copia e cola" : "Linha digitável"}
      </p>
      <code className="bg-muted block rounded-md p-2 text-xs break-all select-all">{code}</code>
      <CopyButton text={code} />
    </div>
  );
}

/** Ações da cobrança conforme a situação e as permissões (a API valida de novo). */
export function ChargeActions({
  charge,
  canManage,
  canResolve,
  sandbox,
}: {
  charge: Charge;
  canManage: boolean;
  canResolve: boolean;
  sandbox: boolean;
}) {
  const pending = charge.status === "pendente";
  return (
    <div className="space-y-3">
      {pending && canManage && sandbox && charge.provider === "sandbox" && (
        <details className="rounded-md border border-dashed p-3">
          <summary className="text-brand-institutional flex cursor-pointer items-center gap-1.5 text-sm font-medium">
            <FlaskConical className="size-4" /> Simular retorno do PSP (sandbox)
          </summary>
          <p className="text-muted-foreground mt-2 text-xs">
            Gera um webhook assinado, processado pelo mesmo caminho de um PSP real. Sem dinheiro de verdade.
          </p>
          <ActionForm
            action={simulateChargeAction.bind(null, charge.id)}
            submitLabel="Enviar webhook"
            submitVariant="outline"
            className="mt-2"
          >
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="Evento" htmlFor={`ev-${charge.id}`}>
                <select id={`ev-${charge.id}`} name="event" className={selectClass} defaultValue="paid">
                  <option value="paid">Pagamento confirmado</option>
                  <option value="expired">Cobrança expirada</option>
                </select>
              </Field>
              <Field label="Valor pago (R$)" htmlFor={`am-${charge.id}`}>
                <Input
                  id={`am-${charge.id}`}
                  name="amount"
                  inputMode="decimal"
                  placeholder="valor da cobrança"
                />
              </Field>
              <Field label="Data do pagamento" htmlFor={`dt-${charge.id}`}>
                <Input id={`dt-${charge.id}`} name="paid_on" type="date" />
              </Field>
            </div>
          </ActionForm>
        </details>
      )}
      {pending && canManage && (
        <details>
          <summary className="text-brand-institutional cursor-pointer text-xs font-medium">
            Cancelar cobrança
          </summary>
          <ActionForm
            action={cancelChargeAction.bind(null, charge.id)}
            submitLabel="Confirmar cancelamento"
            submitVariant="outline"
            className="mt-2 sm:max-w-sm"
          >
            <Field label="Motivo" htmlFor={`cc-${charge.id}`}>
              <Input
                id={`cc-${charge.id}`}
                name="reason"
                required
                minLength={3}
                maxLength={500}
                placeholder="ex.: cliente pediu boleto"
              />
            </Field>
          </ActionForm>
        </details>
      )}
      {charge.status === "divergente" && canResolve && (
        <ActionForm
          action={resolveChargeAction.bind(null, charge.id)}
          submitLabel="Marcar como resolvida"
          submitVariant="outline"
          className="sm:max-w-md"
        >
          <Field label="Como foi resolvida" htmlFor={`rs-${charge.id}`}>
            <Input
              id={`rs-${charge.id}`}
              name="reason"
              required
              minLength={3}
              maxLength={500}
              placeholder="ex.: valor devolvido ao pagador via Pix"
            />
          </Field>
        </ActionForm>
      )}
    </div>
  );
}

/** Painel de cobranças na tela da parcela. */
export function ChargePanel({
  installmentId,
  open,
  charges,
  providers,
  canManage,
  canResolve,
}: {
  installmentId: string;
  open: boolean;
  charges: Charge[];
  providers: PaymentProviderInfo[];
  canManage: boolean;
  canResolve: boolean;
}) {
  const pending = charges.find((c) => c.status === "pendente");
  const history = charges.filter((c) => c !== pending);
  const sandbox = providers.some((p) => p.sandbox);
  const provider = providers[0];

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <QrCode className="text-brand size-4" /> Cobrança Pix / boleto
          </CardTitle>
          <CardDescription>
            A baixa acontece sozinha quando o PSP confirma o pagamento (webhook). Recebimento manual cancela a
            cobrança pendente.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {pending ? (
          <div className="space-y-3 rounded-lg border p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Link
                href={`/app/financeiro/cobrancas/${pending.id}`}
                className="text-brand-institutional text-sm font-semibold hover:underline"
              >
                {CHARGE_METHOD[pending.method]} de {brl(pending.amount)} · vence {dateBR(pending.due_date)}
              </Link>
              <Badge variant="warning">{CHARGE_STATUS.pendente?.label}</Badge>
            </div>
            <ChargeCode charge={pending} />
            <ChargeActions charge={pending} canManage={canManage} canResolve={canResolve} sandbox={sandbox} />
          </div>
        ) : open && canManage ? (
          provider ? (
            <ActionForm
              action={issueChargeAction.bind(null, installmentId)}
              submitLabel="Emitir cobrança"
              className="sm:max-w-md"
            >
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Meio" htmlFor="charge-method">
                  <select id="charge-method" name="method" className={selectClass} defaultValue="pix">
                    <option value="pix">Pix</option>
                    <option value="boleto">Boleto</option>
                  </select>
                </Field>
                <Field label="Vencimento" htmlFor="charge-due">
                  <Input id="charge-due" name="due_date" type="date" />
                </Field>
              </div>
              <p className="text-muted-foreground text-xs">
                Sem vencimento, usa o da parcela (ou hoje, se já venceu — com multa e juros do dia). Provedor:{" "}
                {provider.label}.
              </p>
            </ActionForm>
          ) : (
            <p className="text-muted-foreground text-sm">Nenhum provedor de pagamento habilitado.</p>
          )
        ) : (
          !history.length && <p className="text-muted-foreground text-sm">Nenhuma cobrança emitida.</p>
        )}

        {history.length > 0 && (
          <ul className="divide-y text-sm">
            {history.map((c) => {
              const st = CHARGE_STATUS[c.status];
              return (
                <li key={c.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                  <Link href={`/app/financeiro/cobrancas/${c.id}`} className="hover:underline">
                    {CHARGE_METHOD[c.method]} de {brl(c.amount)}
                    <span className="text-muted-foreground"> · emitida {dateTime(c.created_at)}</span>
                  </Link>
                  <Badge variant={st?.badge ?? "muted"}>{st?.label ?? c.status}</Badge>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
