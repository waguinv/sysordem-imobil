import type { Metadata } from "next";
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
    </div>
  );
}
