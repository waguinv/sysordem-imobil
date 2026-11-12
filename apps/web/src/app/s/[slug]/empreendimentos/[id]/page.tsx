import { ArrowLeft, MapPin } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { LeadForm } from "@/components/site/lead-form";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/primitives";
import { brl, DEV_KIND, m2 } from "@/lib/format";
import { publicApi, type PublicDevelopment, type PublicSite } from "@/lib/public-api";
import { cn } from "@/lib/utils";

type Props = { params: Promise<{ slug: string; id: string }> };

const path = (slug: string, id: string) =>
  `/${encodeURIComponent(slug)}/developments/${encodeURIComponent(id)}`;

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug, id } = await params;
  const [site, d] = await Promise.all([
    publicApi<PublicSite>(`/${encodeURIComponent(slug)}`),
    publicApi<PublicDevelopment>(path(slug, id)),
  ]);
  return { title: { absolute: `${d.name} · ${site.name}` }, description: d.description.slice(0, 160) };
}

export default async function PublicDevelopmentPage({ params }: Props) {
  const { slug, id } = await params;
  const [site, d] = await Promise.all([
    publicApi<PublicSite>(`/${encodeURIComponent(slug)}`),
    publicApi<PublicDevelopment>(path(slug, id)),
  ]);
  const available = d.blocks.flatMap((b) =>
    b.lots
      .filter((l) => l.available)
      .map((l) => ({ id: l.id, label: `Quadra ${b.code} · Lote ${l.number}` })),
  );

  return (
    <div className="space-y-8">
      <Link
        href={`/s/${site.slug}`}
        className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-sm"
      >
        <ArrowLeft className="size-4" /> {site.name}
      </Link>

      <section className="space-y-2">
        <h1 className="text-brand-institutional text-3xl font-bold">{d.name}</h1>
        <p className="text-muted-foreground flex items-center gap-1 text-sm">
          <MapPin className="size-4" />
          {[d.city, d.state].filter(Boolean).join("/") || "—"} · {DEV_KIND[d.kind] ?? d.kind} ·{" "}
          {d.lots_available} de {d.lots_total} lotes disponíveis
          {d.price_from && d.lots_available > 0 && ` · a partir de ${brl(d.price_from)}`}
        </p>
        <p className="max-w-3xl whitespace-pre-line">{d.description}</p>
      </section>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_380px]">
        <section aria-labelledby="lotes" className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 id="lotes" className="text-brand-institutional text-xl font-semibold">
              Lotes
            </h2>
            <div className="text-muted-foreground flex gap-4 text-xs">
              <span className="flex items-center gap-1.5">
                <i className="bg-lot-disponivel size-2.5 rounded-full" /> Disponível
              </span>
              <span className="flex items-center gap-1.5">
                <i className="size-2.5 rounded-full bg-zinc-300" /> Indisponível
              </span>
            </div>
          </div>
          {d.blocks.length === 0 && <p className="text-muted-foreground text-sm">Mapa em preparação.</p>}
          {d.blocks.map((b) => (
            <div key={b.code} className="bg-brand-surface rounded-xl p-3">
              <h3 className="text-brand-institutional mb-2 text-sm font-semibold">Quadra {b.code}</h3>
              <ul className="grid grid-cols-[repeat(auto-fill,minmax(5.5rem,1fr))] gap-2">
                {b.lots.map((l) => (
                  <li
                    key={l.id}
                    className={cn(
                      "rounded-lg px-2 py-1.5 text-xs",
                      l.available ? "bg-lot-disponivel text-brand-deep" : "bg-zinc-200 text-zinc-500",
                    )}
                    title={l.available ? "Disponível" : "Indisponível"}
                  >
                    <span className="block font-semibold">Lote {l.number}</span>
                    <span className="block">{m2(l.area_m2)}</span>
                    {l.price && <span className="block tabular-nums">{brl(l.price)}</span>}
                    {!l.available && <span className="block">indisponível</span>}
                  </li>
                ))}
              </ul>
            </div>
          ))}
          <p className="text-muted-foreground text-xs">
            Disponibilidade atualizada em tempo real. Valores e condições sujeitos a confirmação.
          </p>
        </section>

        {site.lead_form_enabled && (
          <aside>
            <Card>
              <CardHeader>
                <CardTitle>Tenho interesse</CardTitle>
              </CardHeader>
              <CardContent>
                <LeadForm slug={site.slug} siteName={site.name} developmentId={d.id} lots={available} />
              </CardContent>
            </Card>
          </aside>
        )}
      </div>
    </div>
  );
}
