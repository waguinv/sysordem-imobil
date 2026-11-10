import { FileSignature, LandPlot, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { Logo } from "@/components/brand/logo";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid min-h-dvh lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
      <aside className="bg-brand-institutional relative hidden overflow-hidden p-12 text-white lg:flex lg:flex-col lg:justify-between">
        <div className="bg-brand/20 absolute -right-24 -bottom-24 size-96 rounded-full blur-3xl" />
        <Link href="/" aria-label="Início">
          <Logo variant="reverse" className="h-9" priority />
        </Link>
        <div className="relative space-y-8">
          <h2 className="max-w-md text-3xl leading-snug font-bold">
            Lotes, vendas e recebimentos do seu loteamento <span className="text-brand">em ordem</span>.
          </h2>
          <ul className="space-y-4 text-white/80">
            <li className="flex items-center gap-3">
              <LandPlot className="text-brand size-5" /> Mapa de quadras e lotes sempre atualizado
            </li>
            <li className="flex items-center gap-3">
              <FileSignature className="text-brand size-5" /> Contratos, parcelas e comissões sem planilha
            </li>
            <li className="flex items-center gap-3">
              <ShieldCheck className="text-brand size-5" /> Dados isolados por empresa e acesso por perfil
            </li>
          </ul>
        </div>
        <p className="relative text-xs text-white/50">
          © {new Date().getFullYear()} SysOrdem Soluções Digitais
        </p>
      </aside>
      <main className="bg-brand-surface/60 flex flex-col items-center justify-center px-4 py-10 sm:px-8">
        <Link href="/" className="mb-8 lg:hidden" aria-label="Início">
          <Logo className="h-9" priority />
        </Link>
        <div className="w-full max-w-md rounded-2xl border border-black/5 bg-white p-6 shadow-xl shadow-emerald-950/5 sm:p-8">
          {children}
        </div>
      </main>
    </div>
  );
}
