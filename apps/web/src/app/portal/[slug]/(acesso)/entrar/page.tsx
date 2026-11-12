import type { Metadata } from "next";
import { ActionForm, Field } from "@/components/ui/action-form";
import { Input } from "@/components/ui/primitives";
import { portalLoginAction } from "@/lib/actions-portal";

export const metadata: Metadata = { title: "Portal do comprador" };

export default async function PortalLoginPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ expirada?: string }>;
}) {
  const { slug } = await params;
  const { expirada } = await searchParams;
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-brand-institutional text-2xl font-bold">Portal do comprador</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Acompanhe seu contrato, parcelas e pagamentos. O acesso é criado pela empresa: se ainda não tem,
          peça o convite.
        </p>
        {expirada && <p className="mt-2 text-sm text-amber-700">Sua sessão expirou. Entre novamente.</p>}
      </div>
      <ActionForm action={portalLoginAction.bind(null, slug)} submitLabel="Entrar">
        <Field label="E-mail" htmlFor="email">
          <Input id="email" name="email" type="email" autoComplete="username" required />
        </Field>
        <Field label="Senha" htmlFor="password">
          <Input id="password" name="password" type="password" autoComplete="current-password" required />
        </Field>
      </ActionForm>
      <p className="text-muted-foreground text-xs">Esqueceu a senha? Peça um novo convite à empresa.</p>
    </div>
  );
}
