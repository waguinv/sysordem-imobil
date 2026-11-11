import { Mail, MessageCircle, Phone } from "lucide-react";
import Link from "next/link";
import { BrandSymbol } from "@/components/brand/logo";
import { publicApi, type PublicSite } from "@/lib/public-api";

/** Site público da empresa: sem menu do sistema, sem sessão. Dados só pelos DTOs públicos. */
export default async function PublicSiteLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const site = await publicApi<PublicSite>(`/${encodeURIComponent(slug)}`);
  const c = site.contact;
  const wa = c.whatsapp?.replace(/\D/g, "");

  return (
    <div className="flex min-h-dvh flex-col bg-white">
      <header className="bg-brand-institutional text-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-4">
          <Link href={`/s/${site.slug}`} className="text-lg font-bold">
            {site.name}
          </Link>
          <nav className="flex flex-wrap items-center gap-4 text-sm text-white/90">
            {c.phone && (
              <a href={`tel:${c.phone.replace(/[^\d+]/g, "")}`} className="inline-flex items-center gap-1.5">
                <Phone className="size-4" /> {c.phone}
              </a>
            )}
            {wa && (
              <a
                href={`https://wa.me/${wa.length <= 11 ? `55${wa}` : wa}`}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5"
              >
                <MessageCircle className="size-4" /> WhatsApp
              </a>
            )}
          </nav>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">{children}</main>
      <footer className="border-t bg-white">
        <div className="text-muted-foreground mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-6 text-xs">
          <span>
            © {new Date().getFullYear()} {site.name}
            {c.email && (
              <>
                {" · "}
                <a href={`mailto:${c.email}`} className="inline-flex items-center gap-1 underline">
                  <Mail className="size-3" /> {c.email}
                </a>
              </>
            )}
          </span>
          <span className="inline-flex items-center gap-1.5">
            <BrandSymbol className="h-4 w-auto" /> Feito com SysOrdem Imobil
          </span>
        </div>
      </footer>
    </div>
  );
}
