import { CheckCircle2, CircleAlert } from "lucide-react";
import type { Metadata } from "next";
import { redirect } from "next/navigation";
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
import {
  connectPaymentAccountAction,
  disconnectPaymentAccountAction,
  saveCompanyDocumentAction,
} from "@/lib/actions-fase4a";
import { api, can, getMe } from "@/lib/session";
import type { PaymentAccount } from "@/lib/types";

export const metadata: Metadata = { title: "Recebimentos" };

const ENV_LABEL = { sandbox: "Sandbox (testes, sem dinheiro real)", production: "Produção (dinheiro real)" };

function cnpj(d: string | null) {
  if (!d) return "";
  return d.length === 14 ? d.replace(/^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/, "$1.$2.$3/$4-$5") : d;
}

export default async function RecebimentosPage() {
  const me = await getMe();
  if (!can(me, "settings:manage")) redirect("/app");
  const acct = await api<PaymentAccount>("/settings/payments");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-brand-institutional text-2xl font-bold">Recebimentos</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Pix e boleto das parcelas saem pela conta Asaas da própria empresa: o dinheiro cai direto nela, e a
          baixa da parcela acontece sozinha quando o Asaas confirma o pagamento.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-2">
                Conta Asaas
                {acct.configured ? (
                  <Badge variant={acct.environment === "production" ? "default" : "warning"}>
                    {acct.environment === "production" ? "Produção" : "Sandbox"}
                  </Badge>
                ) : (
                  <Badge variant="muted">Não ligada</Badge>
                )}
              </CardTitle>
              <CardDescription>
                A chave de API fica guardada cifrada e nunca volta para a tela. No Asaas: Integrações → Chave
                de API.
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            {!acct.credentials_available && (
              <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-800">
                Este servidor ainda não tem o cofre de credenciais configurado (PAYMENTS_CREDENTIALS_KEY).
                Fale com o suporte antes de ligar a conta.
              </p>
            )}
            {acct.configured && (
              <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
                <dt className="text-muted-foreground">Conta</dt>
                <dd>{acct.account_name ?? "—"}</dd>
                <dt className="text-muted-foreground">Chave</dt>
                <dd className="font-mono">{acct.api_key_hint}</dd>
                <dt className="text-muted-foreground">Aviso de pagamento</dt>
                <dd className="flex items-center gap-1.5">
                  {acct.webhook ? (
                    <>
                      <CheckCircle2 className="size-4 text-emerald-600" /> registrado no Asaas
                    </>
                  ) : (
                    <>
                      <CircleAlert className="size-4 text-amber-600" /> sem webhook — use &quot;Atualizar
                      situação&quot; na cobrança
                    </>
                  )}
                </dd>
                <dt className="text-muted-foreground">Cobranças pendentes</dt>
                <dd>{acct.pending_charges}</dd>
              </dl>
            )}
            <ActionForm
              action={connectPaymentAccountAction}
              submitLabel={acct.configured ? "Trocar chave" : "Ligar conta Asaas"}
              submitVariant={acct.configured ? "outline" : "default"}
            >
              <Field label="Ambiente" htmlFor="asaas-env">
                <select
                  id="asaas-env"
                  name="environment"
                  className={selectClass}
                  defaultValue={acct.environment ?? acct.environments[0]}
                >
                  {acct.environments.map((e) => (
                    <option key={e} value={e}>
                      {ENV_LABEL[e]}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Chave de API do Asaas" htmlFor="asaas-key">
                <Input
                  id="asaas-key"
                  name="api_key"
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  required
                  minLength={20}
                  maxLength={400}
                  placeholder="$aact_…"
                />
              </Field>
              <p className="text-muted-foreground text-xs">
                Ao salvar, o sistema confere a chave no Asaas e registra o aviso de pagamento (webhook) na
                conta da empresa. A chave de produção começa com <code>$aact_prod_</code>.
              </p>
            </ActionForm>
            {acct.configured && (
              <details>
                <summary className="text-brand-institutional cursor-pointer text-xs font-medium">
                  Desligar conta Asaas
                </summary>
                <ActionForm
                  action={disconnectPaymentAccountAction}
                  submitLabel="Desligar"
                  submitVariant="outline"
                  className="mt-2 sm:max-w-sm"
                >
                  <p className="text-muted-foreground text-xs">
                    Exige nenhuma cobrança pendente no Asaas. Cobranças já pagas continuam no histórico.
                  </p>
                  <Field label="Motivo" htmlFor="asaas-off">
                    <Input id="asaas-off" name="reason" required minLength={3} maxLength={500} />
                  </Field>
                </ActionForm>
              </details>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>CNPJ da empresa</CardTitle>
              <CardDescription>
                Usado na fatura da assinatura do SysOrdem Imobil (Pix/boleto emitidos em nome da empresa).
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <ActionForm action={saveCompanyDocumentAction} submitLabel="Salvar CNPJ" submitVariant="outline">
              <Field label="CNPJ" htmlFor="company-doc">
                <Input
                  id="company-doc"
                  name="document"
                  inputMode="numeric"
                  defaultValue={cnpj(acct.company_document)}
                  placeholder="00.000.000/0000-00"
                  required
                  maxLength={20}
                />
              </Field>
            </ActionForm>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
