import { LogOut } from "lucide-react";
import Link from "next/link";
import { redirect } from "next/navigation";
import { portalLogoutAction } from "@/lib/actions-portal";
import { portalApi, type PortalMe } from "@/lib/portal";

/** Área logada do comprador: cabeçalho simples, sem o menu da equipe. */
export default async function PortalAreaLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const me = await portalApi<PortalMe>(slug, "/me");
  // sessão de outra empresa: leva para o portal certo (o token é que manda, nunca a URL)
  if (me.company_slug !== slug) redirect(`/portal/${me.company_slug}`);
  return (
    <div className="flex min-h-dvh flex-col bg-white">
      <header className="bg-brand-institutional text-white">
        <div className="mx-auto flex max-w-4xl flex-wrap items-center justify-between gap-3 px-4 py-4">
          <Link href={`/portal/${slug}`} className="min-w-0">
            <span className="block truncate text-lg font-bold">{me.company_name}</span>
            <span className="block text-xs text-white/70">Portal do comprador · {me.customer_name}</span>
          </Link>
          <nav className="flex items-center gap-4 text-sm">
            <Link href={`/portal/${slug}`} className="text-white/90 hover:text-white">
              Meus contratos
            </Link>
            <Link href={`/portal/${slug}/informe`} className="text-white/90 hover:text-white">
              Informe de pagamentos
            </Link>
            <form action={portalLogoutAction.bind(null, slug)}>
              <button type="submit" className="inline-flex items-center gap-1 text-white/90 hover:text-white">
                <LogOut className="size-4" /> Sair
              </button>
            </form>
          </nav>
        </div>
      </header>
      <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-8">{children}</main>
      <footer className="text-muted-foreground border-t py-4 text-center text-xs">
        Dúvidas sobre valores ou contrato? Fale com {me.company_name}.
      </footer>
    </div>
  );
}
