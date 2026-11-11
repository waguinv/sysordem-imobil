import { LandPlot, MapPin } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { LeadForm } from "@/components/site/lead-form";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/primitives";
import { brl, DEV_KIND } from "@/lib/format";
import { publicApi, type PublicSite } from "@/lib/public-api";

type Props = { params: Promise<{ slug: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const site = await publicApi<PublicSite>(`/${encodeURIComponent(slug)}`);
  return { title: { absolute: site.name }, description: site.tagline ?? `Lotes à venda — ${site.name}` };
}

export default async function PublicHome({ params }: Props) {
  const { slug } = await params;
  const site = await publicApi<PublicSite>(`/${encodeURIComponent(slug)}`);

  return (
    <div className="space-y-10">
      <section className="space-y-2">
        <h1 className="text-brand-institutional text-3xl font-bold sm:text-4xl">{site.name}</h1>
        {site.tagline && <p className="text-muted-foreground text-lg">{site.tagline}</p>}
      </section>

      <section aria-labelledby="empreendimentos" className="space-y-4">
        <h2 id="empreendimentos" className="text-brand-institutional text-xl font-semibold">
          Empreendimentos
        </h2>
        {site.developments.length === 0 ? (
          <p className="text-muted-foreground text-sm">Em breve, novos lançamentos.</p>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {site.developments.map((d) => (
              <Link
                key={d.id}
                href={`/s/${site.slug}/empreendimentos/${d.id}`}
                className="group focus-visible:ring-ring/50 rounded-xl outline-none focus-visible:ring-[3px]"
              >
                <Card className="h-full transition-shadow group-hover:shadow-md">
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <LandPlot className="text-brand size-4" /> {d.name}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2 text-sm">
                    <p className="text-muted-foreground flex items-center gap-1">
                      <MapPin className="size-3.5" />
                      {[d.city, d.state].filter(Boolean).join("/") || "—"} · {DEV_KIND[d.kind] ?? d.kind}
                    </p>
                    <p className="line-clamp-3">{d.description}</p>
                    <p className="font-medium">
                      {d.lots_available > 0
                        ? `${d.lots_available} lote(s) disponível(is)`
                        : "Todos os lotes vendidos ou reservados"}
                      {d.price_from && d.lots_available > 0 && (
                        <span className="text-brand-institutional"> · a partir de {brl(d.price_from)}</span>
                      )}
                    </p>
                  </CardContent>
                </Card>
              </Link>
            ))}
          </div>
        )}
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        {site.about && (
          <section aria-labelledby="sobre" className="space-y-2">
            <h2 id="sobre" className="text-brand-institutional text-xl font-semibold">
              Sobre nós
            </h2>
            <p className="text-sm whitespace-pre-line">{site.about}</p>
          </section>
        )}
        {site.lead_form_enabled && (
          <section aria-labelledby="contato">
            <Card>
              <CardHeader>
                <CardTitle id="contato">Fale com a gente</CardTitle>
              </CardHeader>
              <CardContent>
                <LeadForm slug={site.slug} siteName={site.name} />
              </CardContent>
            </Card>
          </section>
        )}
      </div>
    </div>
  );
}
