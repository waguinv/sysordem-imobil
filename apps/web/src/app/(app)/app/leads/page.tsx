import { Search, UserPlus } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
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
import { createLeadAction } from "@/lib/actions-fase2c";
import { dateTime, LEAD_SOURCE, LEAD_STATUS } from "@/lib/format";
import { api, can, getMe } from "@/lib/session";
import type { Broker, Development, Lead } from "@/lib/types";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Leads" };

const FILTERS = [
  { key: "", label: "Todos" },
  { key: "novo", label: "Novos" },
  { key: "em_contato", label: "Em contato" },
  { key: "qualificado", label: "Qualificados" },
  { key: "convertido", label: "Convertidos" },
  { key: "perdido", label: "Perdidos" },
];

export default async function LeadsPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string; q?: string }>;
}) {
  const me = await getMe();
  if (!can(me, "leads:view")) redirect("/app");
  const { status = "", q = "" } = await searchParams;
  const valid = FILTERS.some((f) => f.key === status) ? status : "";
  const qs = new URLSearchParams();
  if (valid) qs.set("status", valid);
  if (q) qs.set("q", q);
  const manage = can(me, "leads:manage");
  const isBroker = me.role === "corretor";
  const [leads, developments, brokers] = await Promise.all([
    api<Lead[]>(`/leads${qs.size ? `?${qs}` : ""}`),
    manage ? api<Development[]>("/developments") : Promise.resolve([] as Development[]),
    manage && can(me, "brokers:view")
      ? api<Broker[]>("/brokers?active=true")
      : Promise.resolve([] as Broker[]),
  ]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-brand-institutional text-2xl font-bold">Leads</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          {isBroker
            ? "Seus interessados. Registre cada contato e converta em comprador quando a venda avançar."
            : "Interessados de todos os corretores. Distribua, acompanhe os contatos e converta em comprador."}
        </p>
      </div>

      <nav className="flex flex-wrap gap-2" aria-label="Filtrar leads">
        {FILTERS.map((f) => (
          <Link
            key={f.key || "todos"}
            href={`/app/leads${f.key ? `?status=${f.key}` : ""}`}
            aria-current={valid === f.key ? "page" : undefined}
            className={cn(
              "rounded-full border px-3 py-1 text-sm",
              valid === f.key
                ? "bg-brand-institutional border-brand-institutional text-white"
                : "hover:bg-accent bg-white",
            )}
          >
            {f.label}
          </Link>
        ))}
      </nav>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
        <Card>
          <CardContent className="space-y-3">
            <form className="relative" role="search">
              {valid && <input type="hidden" name="status" value={valid} />}
              <Search className="text-muted-foreground absolute top-3 left-3 size-4" />
              <Input
                name="q"
                defaultValue={q}
                placeholder="Buscar por nome, e-mail ou telefone"
                className="pl-9"
              />
            </form>
            {leads.length === 0 ? (
              <div className="flex flex-col items-center gap-2 py-8 text-center">
                <UserPlus className="text-brand-muted size-8" />
                <p className="text-muted-foreground text-sm">Nenhum lead nesta lista.</p>
              </div>
            ) : (
              <ul className="divide-y">
                {leads.map((l) => {
                  const st = LEAD_STATUS[l.status]!;
                  return (
                    <li key={l.id}>
                      <Link
                        href={`/app/leads/${l.id}`}
                        className="hover:bg-accent/60 -mx-2 flex flex-wrap items-center justify-between gap-2 rounded-lg px-2 py-3"
                      >
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">{l.name}</p>
                          <p className="text-muted-foreground truncate text-xs">
                            {[
                              LEAD_SOURCE[l.source],
                              l.development_name,
                              !isBroker && (l.broker_name ?? "sem corretor"),
                              `atualizado ${dateTime(l.updated_at)}`,
                            ]
                              .filter(Boolean)
                              .join(" · ")}
                          </p>
                        </div>
                        <Badge variant={st.badge}>{st.label}</Badge>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            )}
          </CardContent>
        </Card>

        {manage && (
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Novo lead</CardTitle>
                <CardDescription>Basta nome e um contato. CPF só quando virar comprador.</CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              <ActionForm action={createLeadAction} submitLabel="Cadastrar">
                <Field label="Nome" htmlFor="name">
                  <Input id="name" name="name" required minLength={2} maxLength={160} />
                </Field>
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Telefone" htmlFor="phone">
                    <Input id="phone" name="phone" type="tel" maxLength={30} />
                  </Field>
                  <Field label="E-mail" htmlFor="email">
                    <Input id="email" name="email" type="email" maxLength={254} />
                  </Field>
                </div>
                <Field label="Origem" htmlFor="source">
                  <select id="source" name="source" className={selectClass} defaultValue="site">
                    {Object.entries(LEAD_SOURCE).map(([v, l]) => (
                      <option key={v} value={v}>
                        {l}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Empreendimento de interesse" htmlFor="development_id">
                  <select id="development_id" name="development_id" className={selectClass} defaultValue="">
                    <option value="">— não informado —</option>
                    {developments.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name}
                      </option>
                    ))}
                  </select>
                </Field>
                {!isBroker && (
                  <Field label="Corretor responsável" htmlFor="broker_id">
                    <select id="broker_id" name="broker_id" className={selectClass} defaultValue="">
                      <option value="">— distribuir depois —</option>
                      {brokers.map((b) => (
                        <option key={b.id} value={b.id}>
                          {b.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                )}
                <Field label="Observações" htmlFor="notes">
                  <Input id="notes" name="notes" maxLength={4000} />
                </Field>
              </ActionForm>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
