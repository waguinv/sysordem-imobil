import type { Metadata } from "next";
import Link from "next/link";
import { ActionForm, Field } from "@/components/ui/action-form";
import { Input } from "@/components/ui/primitives";
import { portalActivateAction } from "@/lib/actions-portal";

export const metadata: Metadata = { title: "Ativar acesso" };

export default async function PortalActivatePage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ token?: string }>;
}) {
  const { slug } = await params;
  const { token = "" } = await searchParams;
  if (token.length < 20) {
    return (
      <div className="space-y-3">
        <h1 className="text-brand-institutional text-2xl font-bold">Link incompleto</h1>
        <p className="text-muted-foreground text-sm">
          Abra o link do convite exatamente como recebeu ou peça um novo à empresa.
        </p>
        <Link href={`/portal/${slug}/entrar`} className="text-brand-institutional text-sm underline">
          Já tenho senha
        </Link>
      </div>
    );
  }
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-brand-institutional text-2xl font-bold">Crie sua senha</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Este link vale uma única vez. Use pelo menos 8 caracteres.
        </p>
      </div>
      <ActionForm action={portalActivateAction.bind(null, slug, token)} submitLabel="Ativar acesso">
        <Field label="Nova senha" htmlFor="password">
          <Input
            id="password"
            name="password"
            type="password"
            autoComplete="new-password"
            minLength={8}
            required
          />
        </Field>
        <Field label="Repita a senha" htmlFor="confirm">
          <Input
            id="confirm"
            name="confirm"
            type="password"
            autoComplete="new-password"
            minLength={8}
            required
          />
        </Field>
      </ActionForm>
    </div>
  );
}
