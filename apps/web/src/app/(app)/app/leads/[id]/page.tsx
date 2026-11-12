import { ArrowLeft, MessageSquare } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
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
  addActivityAction,
  changeLeadStatusAction,
  convertLeadAction,
  reassignLeadAction,
} from "@/lib/actions-fase2c";
import {
  ACTIVITY_KIND,
  brl,
  dateBR,
  dateTime,
  LEAD_SOURCE,
  LEAD_STATUS,
  PROPOSAL_STATUS,
} from "@/lib/format";
import { api, ApiError, can, getMe } from "@/lib/session";
import type { Broker, Customer, LeadDetail, Proposal } from "@/lib/types";

export const metadata: Metadata = { title: "Lead" };

const OPEN_STATUSES = ["novo", "em_contato", "qualificado", "perdido"] as const;

export default async function LeadPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const me = await getMe();
  let lead: LeadDetail;
  try {
    lead = await api<LeadDetail>(`/leads/${encodeURIComponent(id)}`);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 422)) notFound();
    throw e;
  }
  const manage = can(me, "leads:manage");
  const isBroker = me.role === "corretor";
  const converted = lead.status === "convertido";
  const canConvert = manage && can(me, "customers:manage") && !converted && lead.status !== "perdido";
  const [brokers, customers, proposals] = await Promise.all([
    manage && !isBroker && can(me, "brokers:view") && !converted
      ? api<Broker[]>("/brokers?active=true")
      : Promise.resolve([] as Broker[]),
    canConvert ? api<Customer[]>("/customers?limit=500") : Promise.resolve([] as Customer[]),
    converted && can(me, "proposals:view")
      ? api<Proposal[]>(`/proposals?lead_id=${lead.id}`)
      : Promise.resolve([] as Proposal[]),
  ]);
  const st = LEAD_STATUS[lead.status]!;

  return (
    <div className="space-y-6">
      <Link
        href="/app/leads"
        className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-sm"
      >
        <ArrowLeft className="size-4" /> Leads
      </Link>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-brand-institutional text-2xl font-bold">{lead.name}</h1>
          <p className="text-muted-foreground text-sm">
            {[lead.phone, lead.email].filter(Boolean).join(" · ")} · desde {dateTime(lead.created_at)}
          </p>
        </div>
        <Badge variant={st.badge}>{st.label}</Badge>
      </div>

      <Card>
        <CardContent>
          <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
            <div>
              <dt className="text-muted-foreground text-xs">Origem</dt>
              <dd className="font-medium">{LEAD_SOURCE[lead.source] ?? lead.source}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground text-xs">Interesse</dt>
              <dd className="font-medium">{lead.development_name ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground text-xs">Corretor</dt>
              <dd className="font-medium">{lead.broker_name ?? "sem corretor"}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground text-xs">Situação</dt>
              <dd className="font-medium">
                {st.label}
                {lead.lost_reason && (
                  <span className="text-muted-foreground font-normal"> · {lead.lost_reason}</span>
                )}
              </dd>
            </div>
          </dl>
          {lead.notes && <p className="text-muted-foreground mt-3 text-sm">{lead.notes}</p>}
        </CardContent>
      </Card>

      {converted && (
        <Card className="border-brand/40">
          <CardHeader>
            <div>
              <CardTitle>Comprador cadastrado</CardTitle>
              <CardDescription>
                Para fazer uma proposta, abra o lote no mapa do empreendimento e use “Nova proposta”.
              </CardDescription>
            </div>
            {lead.development_id && (
              <Link
                href={`/app/empreendimentos/${lead.development_id}`}
                className="text-brand-institutional text-sm font-medium underline"
              >
                Abrir mapa de {lead.development_name}
              </Link>
            )}
          </CardHeader>
          {proposals.length > 0 && (
            <CardContent>
              <ul className="divide-y">
                {proposals.map((p) => (
                  <li key={p.id}>
                    <Link
                      href={`/app/propostas/${p.id}`}
                      className="hover:bg-accent/60 -mx-2 flex flex-wrap items-center justify-between gap-2 rounded-lg px-2 py-2.5 text-sm"
                    >
                      <span>
                        Q{p.block_code} · Lote {p.lot_number} · {brl(p.total_price)}
                        <span className="text-muted-foreground"> · válida até {dateBR(p.valid_until)}</span>
                      </span>
                      <Badge variant={PROPOSAL_STATUS[p.status]!.badge}>
                        {PROPOSAL_STATUS[p.status]!.label}
                      </Badge>
                    </Link>
                  </li>
                ))}
              </ul>
            </CardContent>
          )}
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
        <Card>
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-2">
                <MessageSquare className="text-brand size-4" /> Contatos e histórico
              </CardTitle>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            {manage && (
              <ActionForm
                action={addActivityAction.bind(null, lead.id)}
                submitLabel="Registrar"
                resetOnSuccess
                className="grid gap-3 xl:grid-cols-[160px_minmax(0,1fr)] [&>button]:w-fit [&>button]:xl:col-span-2 [&>p]:xl:col-span-2"
              >
                <Field label="Tipo" htmlFor="kind">
                  <select id="kind" name="kind" className={selectClass} defaultValue="ligacao">
                    {Object.entries(ACTIVITY_KIND)
                      .filter(([k]) => k !== "status")
                      .map(([v, l]) => (
                        <option key={v} value={v}>
                          {l}
                        </option>
                      ))}
                  </select>
                </Field>
                <Field label="O que aconteceu" htmlFor="description">
                  <Input id="description" name="description" required minLength={2} maxLength={4000} />
                </Field>
              </ActionForm>
            )}
            <ol className="divide-y border-t">
              {lead.activities.map((a) => (
                <li key={a.id} className="py-2.5 text-sm">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span>
                      <strong className={a.kind === "status" ? "text-muted-foreground font-medium" : ""}>
                        {ACTIVITY_KIND[a.kind] ?? a.kind}
                      </strong>{" "}
                      · {a.description}
                    </span>
                    <span className="text-muted-foreground text-xs">
                      {a.author_name ?? "sistema"} · {dateTime(a.created_at)}
                    </span>
                  </div>
                </li>
              ))}
            </ol>
          </CardContent>
        </Card>

        <div className="space-y-4">
          {canConvert && (
            <Card>
              <CardHeader>
                <div>
                  <CardTitle>Converter em comprador</CardTitle>
                  <CardDescription>
                    Cria o comprador com nome e contato do lead, ou liga a um já cadastrado.
                  </CardDescription>
                </div>
              </CardHeader>
              <CardContent>
                <ActionForm action={convertLeadAction.bind(null, lead.id)} submitLabel="Converter">
                  {customers.length > 0 && (
                    <Field label="Comprador já cadastrado (opcional)" htmlFor="customer_id">
                      <select id="customer_id" name="customer_id" className={selectClass} defaultValue="">
                        <option value="">— cadastrar novo —</option>
                        {customers.map((c) => (
                          <option key={c.id} value={c.id}>
                            {c.name}
                          </option>
                        ))}
                      </select>
                    </Field>
                  )}
                  <Field label="CPF ou CNPJ (novo comprador)" htmlFor="document">
                    <Input id="document" name="document" inputMode="numeric" maxLength={20} />
                  </Field>
                  <div className="grid grid-cols-[1fr_80px] gap-3">
                    <Field label="Cidade" htmlFor="city">
                      <Input id="city" name="city" maxLength={120} />
                    </Field>
                    <Field label="UF" htmlFor="state">
                      <Input id="state" name="state" maxLength={2} className="uppercase" />
                    </Field>
                  </div>
                </ActionForm>
              </CardContent>
            </Card>
          )}

          {manage && !converted && (
            <Card>
              <CardHeader>
                <div>
                  <CardTitle>Situação</CardTitle>
                </div>
              </CardHeader>
              <CardContent>
                <ActionForm action={changeLeadStatusAction.bind(null, lead.id)} submitLabel="Atualizar">
                  <Field label="Nova situação" htmlFor="status">
                    <select id="status" name="status" className={selectClass} defaultValue={lead.status}>
                      {OPEN_STATUSES.map((s) => (
                        <option key={s} value={s}>
                          {LEAD_STATUS[s]!.label}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Motivo da perda (se perdido)" htmlFor="lost_reason">
                    <Input id="lost_reason" name="lost_reason" maxLength={500} />
                  </Field>
                </ActionForm>
              </CardContent>
            </Card>
          )}

          {brokers.length > 0 && (
            <Card>
              <CardHeader>
                <div>
                  <CardTitle>Corretor responsável</CardTitle>
                </div>
              </CardHeader>
              <CardContent>
                <ActionForm action={reassignLeadAction.bind(null, lead.id)} submitLabel="Salvar">
                  <select
                    name="broker_id"
                    aria-label="Corretor responsável"
                    className={selectClass}
                    defaultValue={lead.broker_id ?? ""}
                  >
                    <option value="">— sem corretor —</option>
                    {brokers.map((b) => (
                      <option key={b.id} value={b.id}>
                        {b.name}
                      </option>
                    ))}
                  </select>
                </ActionForm>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
