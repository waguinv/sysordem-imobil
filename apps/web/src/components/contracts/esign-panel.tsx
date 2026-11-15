import { FileText, PenLine } from "lucide-react";
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
import { cancelSignatureAction, sendForSignatureAction, simulateSignatureAction } from "@/lib/actions-esign";
import { dateTime } from "@/lib/format";
import type { EsignProvider, SignatureRequest } from "@/lib/types";

const STATUS: Record<
  SignatureRequest["status"],
  { label: string; badge: "default" | "muted" | "warning" | "outline" }
> = {
  enviando: { label: "Enviando", badge: "warning" },
  enviado: { label: "Aguardando assinaturas", badge: "warning" },
  assinado: { label: "Assinado", badge: "default" },
  recusado: { label: "Recusado", badge: "muted" },
  cancelado: { label: "Cancelado", badge: "muted" },
  erro: { label: "Não aplicado", badge: "muted" },
};

const SIGNER: Record<string, string> = { pendente: "pendente", assinou: "assinou", recusou: "recusou" };
const ROLE: Record<string, string> = { comprador: "Comprador(a)", empresa: "Pela empresa" };

/** Assinatura eletrônica do contrato: enviar, acompanhar quem já assinou, cancelar e (fora de produção) simular. */
export function EsignPanel({
  contractId,
  version,
  draft,
  canSign,
  providers,
  requests,
}: {
  contractId: string;
  version: number;
  draft: boolean;
  canSign: boolean;
  providers: EsignProvider[];
  requests: SignatureRequest[];
}) {
  const active = requests.find((r) => r.status === "enviando" || r.status === "enviado");
  const label = (name: string) => providers.find((p) => p.name === name)?.label ?? name;
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <PenLine className="text-brand size-4" /> Assinatura eletrônica
          </CardTitle>
          <CardDescription>
            O comprador e o representante da empresa recebem o link por e-mail. Quando todos assinam, o
            sistema confirma com o serviço de assinatura e o contrato é assinado automaticamente.
          </CardDescription>
        </div>
        <a
          href={`/app/contratos/${contractId}/documento`}
          target="_blank"
          rel="noopener"
          className="text-brand-institutional inline-flex shrink-0 items-center gap-1 text-sm font-medium hover:underline"
        >
          <FileText className="size-4" /> Ver PDF
        </a>
      </CardHeader>
      <CardContent className="space-y-4">
        {active ? (
          <div className="space-y-3 rounded-lg border p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm font-medium">
                Versão {active.contract_version} · {label(active.provider)}
              </p>
              <Badge variant={STATUS[active.status].badge}>{STATUS[active.status].label}</Badge>
            </div>
            <ul className="space-y-1 text-sm">
              {active.signers.map((s) => (
                <li key={s.email} className="flex flex-wrap justify-between gap-2">
                  <span>
                    {ROLE[s.role] ?? s.role}: <strong>{s.name}</strong>{" "}
                    <span className="text-muted-foreground">({s.email})</span>
                  </span>
                  <span
                    className={
                      s.status === "assinou"
                        ? "text-brand-institutional font-medium"
                        : "text-muted-foreground"
                    }
                  >
                    {SIGNER[s.status] ?? s.status}
                  </span>
                </li>
              ))}
            </ul>
            <p className="text-muted-foreground text-xs">
              Enviado em {dateTime(active.created_at)} · documento SHA-256{" "}
              {active.document_sha256.slice(0, 16)}…
            </p>
            {canSign && (
              <div className="grid gap-3 border-t pt-3 sm:grid-cols-2">
                <ActionForm
                  action={cancelSignatureAction.bind(null, active.id, contractId)}
                  submitLabel="Cancelar envio"
                  submitVariant="outline"
                >
                  <Field label="Motivo do cancelamento" htmlFor="esign-cancel">
                    <Input id="esign-cancel" name="reason" required minLength={3} maxLength={500} />
                  </Field>
                </ActionForm>
                {active.provider === "sandbox" && (
                  <div className="space-y-2">
                    <p className="text-muted-foreground text-xs">
                      Simulador (somente teste, sem validade jurídica):
                    </p>
                    <div className="flex flex-wrap gap-2">
                      <ActionForm
                        action={simulateSignatureAction.bind(null, active.id, contractId, "sign_all")}
                        submitLabel="Simular: todos assinam"
                        submitVariant="institutional"
                      >
                        {null}
                      </ActionForm>
                      <ActionForm
                        action={simulateSignatureAction.bind(null, active.id, contractId, "reject")}
                        submitLabel="Simular: recusa"
                        submitVariant="outline"
                      >
                        {null}
                      </ActionForm>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        ) : draft && canSign ? (
          providers.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              Assinatura eletrônica não configurada neste ambiente (ver docs/ASSINATURA_ELETRONICA.md).
            </p>
          ) : (
            <ActionForm
              action={sendForSignatureAction.bind(null, contractId, version)}
              submitLabel={`Enviar versão ${version} para assinatura`}
              submitVariant="institutional"
            >
              {providers.length > 1 && (
                <Field label="Serviço de assinatura" htmlFor="esign-provider">
                  <select
                    id="esign-provider"
                    name="provider"
                    className={selectClass}
                    defaultValue={providers[0]!.name}
                  >
                    {providers.map((p) => (
                      <option key={p.name} value={p.name}>
                        {p.label}
                      </option>
                    ))}
                  </select>
                </Field>
              )}
              <Field label="Mensagem no e-mail (opcional)" htmlFor="esign-message">
                <Input id="esign-message" name="message" maxLength={1000} />
              </Field>
            </ActionForm>
          )
        ) : (
          !requests.length && (
            <p className="text-muted-foreground text-sm">
              {draft ? "Somente proprietário ou administrador envia para assinatura." : "Nenhum envio."}
            </p>
          )
        )}

        {requests.filter((r) => r !== active).length > 0 && (
          <ol className="divide-y border-t pt-2 text-sm">
            {requests
              .filter((r) => r !== active)
              .map((r) => (
                <li key={r.id} className="flex flex-wrap items-baseline justify-between gap-2 py-2">
                  <span>
                    v{r.contract_version} · {label(r.provider)}
                    {r.detail && <span className="text-muted-foreground"> · {r.detail}</span>}
                  </span>
                  <span className="flex items-center gap-2">
                    <span className="text-muted-foreground text-xs">
                      {dateTime(r.closed_at ?? r.created_at)}
                    </span>
                    <Badge variant={STATUS[r.status].badge}>{STATUS[r.status].label}</Badge>
                  </span>
                </li>
              ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
