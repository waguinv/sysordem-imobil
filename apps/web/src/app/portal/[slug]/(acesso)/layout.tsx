import { Logo } from "@/components/brand/logo";

/** Telas de acesso do portal do comprador (sem sessão). */
export default function PortalAccessLayout({ children }: { children: React.ReactNode }) {
  return (
    <main className="bg-brand-surface/60 flex min-h-dvh flex-col items-center justify-center px-4 py-10">
      <Logo className="mb-8 h-9" priority />
      <div className="w-full max-w-md rounded-2xl border border-black/5 bg-white p-6 shadow-xl shadow-emerald-950/5 sm:p-8">
        {children}
      </div>
      <p className="text-muted-foreground mt-6 max-w-md text-center text-xs">
        Portal do comprador. Seus dados são vistos só por você e pela empresa responsável pelo loteamento.
      </p>
    </main>
  );
}
