import type { Metadata } from "next";
import Link from "next/link";
import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Entrar" };

export default async function EntrarPage({ searchParams }: { searchParams: Promise<{ expirada?: string }> }) {
  const { expirada } = await searchParams;
  return (
    <div className="space-y-6">
      <div className="space-y-1.5">
        <h1 className="text-brand-institutional text-2xl font-bold">Entrar</h1>
        <p className="text-muted-foreground text-sm">Acesse o painel da sua empresa.</p>
      </div>
      {expirada && (
        <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">
          Sua sessão expirou ou o acesso foi alterado. Entre novamente.
        </p>
      )}
      <LoginForm />
      <p className="text-muted-foreground text-center text-sm">
        Ainda não usa o SysOrdem Imobil?{" "}
        <Link href="/cadastro" className="text-brand-institutional font-medium underline">
          Criar conta da empresa
        </Link>
      </p>
    </div>
  );
}
