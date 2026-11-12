import { ExternalLink, Globe } from "lucide-react";
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
import { publishDevelopmentAction, saveSiteAction } from "@/lib/actions-fase5a";
import { DEV_STATUS, dateTime } from "@/lib/format";
import { api, can, getMe } from "@/lib/session";

export const metadata: Metadata = { title: "Site público" };

type SiteSettings = {
  slug: string;
  published: boolean;
  title: string | null;
  tagline: string | null;
  about: string | null;
  contact_phone: string | null;
  contact_whatsapp: string | null;
  contact_email: string | null;
  show_prices: boolean;
  lead_form_enabled: boolean;
  updated_at: string | null;
};

type SiteDev = {
  id: string;
  name: string;
  status: string;
  city: string | null;
  state: string | null;
  site_published: boolean;
  site_description: string | null;
  site_published_at: string | null;
};

const textarea = `${selectClass} h-auto py-2`;

function Check({ name, label, defaultChecked }: { name: string; label: string; defaultChecked: boolean }) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <input type="checkbox" name={name} defaultChecked={defaultChecked} className="size-4" /> {label}
    </label>
  );
}

export default async function SitePage() {
  const me = await getMe();
  if (!can(me, "site:manage")) redirect("/app");
  const [s, devs] = await Promise.all([
    api<SiteSettings>("/site/settings"),
    api<SiteDev[]>("/site/developments"),
  ]);
  const publicPath = `/s/${s.slug}`;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-brand-institutional text-2xl font-bold">Site público</h1>
          <p className="text-muted-foreground mt-1 max-w-2xl text-sm">
            Só vai para o site o que você publicar aqui. Lotes aparecem como disponível ou indisponível —
            nunca comprador, contrato, reserva ou situação de pagamento.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Badge variant={s.published ? "default" : "muted"}>{s.published ? "No ar" : "Fora do ar"}</Badge>
          {s.published && (
            <Link
              href={publicPath}
              target="_blank"
              className="text-brand-institutional inline-flex items-center gap-1 text-sm underline"
            >
              Abrir site <ExternalLink className="size-3.5" />
            </Link>
          )}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Card>
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-2">
                <Globe className="text-brand size-4" /> Dados do site
              </CardTitle>
              <CardDescription>
                Endereço: <code className="text-xs">{publicPath}</code>
                {s.updated_at && ` · alterado ${dateTime(s.updated_at)}`}
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <ActionForm action={saveSiteAction.bind(null, s.slug)} submitLabel="Salvar site">
              <Field label="Nome no site" htmlFor="title">
                <Input
                  id="title"
                  name="title"
                  maxLength={120}
                  defaultValue={s.title ?? ""}
                  placeholder={me.tenant.name}
                />
              </Field>
              <Field label="Chamada" htmlFor="tagline">
                <Input id="tagline" name="tagline" maxLength={200} defaultValue={s.tagline ?? ""} />
              </Field>
              <Field label="Sobre a empresa" htmlFor="about">
                <textarea
                  id="about"
                  name="about"
                  rows={4}
                  maxLength={5000}
                  defaultValue={s.about ?? ""}
                  className={textarea}
                />
              </Field>
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Telefone" htmlFor="contact_phone">
                  <Input
                    id="contact_phone"
                    name="contact_phone"
                    maxLength={30}
                    defaultValue={s.contact_phone ?? ""}
                  />
                </Field>
                <Field label="WhatsApp" htmlFor="contact_whatsapp">
                  <Input
                    id="contact_whatsapp"
                    name="contact_whatsapp"
                    maxLength={30}
                    defaultValue={s.contact_whatsapp ?? ""}
                    placeholder="11999990000"
                  />
                </Field>
                <Field label="E-mail" htmlFor="contact_email">
                  <Input
                    id="contact_email"
                    name="contact_email"
                    type="email"
                    maxLength={254}
                    defaultValue={s.contact_email ?? ""}
                  />
                </Field>
              </div>
              <div className="space-y-2">
                <Check
                  name="show_prices"
                  label="Mostrar preço dos lotes disponíveis"
                  defaultChecked={s.show_prices}
                />
                <Check
                  name="lead_form_enabled"
                  label="Formulário de contato (vira lead com origem “Site”)"
                  defaultChecked={s.lead_form_enabled}
                />
                <Check name="published" label="Site no ar" defaultChecked={s.published} />
              </div>
            </ActionForm>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>Empreendimentos no site</CardTitle>
              <CardDescription>
                O texto público é separado da descrição interna. Disponibilidade dos lotes é atualizada na
                hora.
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            {devs.length === 0 ? (
              <p className="text-muted-foreground text-sm">Cadastre um empreendimento primeiro.</p>
            ) : (
              <ul className="divide-y">
                {devs.map((d) => (
                  <li key={d.id} className="space-y-2 py-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-sm font-medium">
                        {d.name}
                        <span className="text-muted-foreground font-normal">
                          {" "}
                          · {DEV_STATUS[d.status] ?? d.status}
                          {d.city && ` · ${d.city}${d.state ? `/${d.state}` : ""}`}
                        </span>
                      </span>
                      <Badge variant={d.site_published ? "default" : "muted"}>
                        {d.site_published ? "Publicado" : "Não publicado"}
                      </Badge>
                    </div>
                    <details>
                      <summary className="text-brand-institutional cursor-pointer text-xs font-medium">
                        {d.site_published ? "Editar ou tirar do site" : "Publicar"}
                      </summary>
                      <ActionForm
                        action={publishDevelopmentAction.bind(null, s.slug, d.id)}
                        submitLabel="Salvar"
                        className="mt-2"
                      >
                        <Field label="Texto público" htmlFor={`desc-${d.id}`}>
                          <textarea
                            id={`desc-${d.id}`}
                            name="description"
                            rows={3}
                            maxLength={5000}
                            defaultValue={d.site_description ?? ""}
                            className={textarea}
                            placeholder="Localização, infraestrutura, tamanhos dos lotes…"
                          />
                        </Field>
                        <Check name="published" label="Publicado no site" defaultChecked={d.site_published} />
                      </ActionForm>
                    </details>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
