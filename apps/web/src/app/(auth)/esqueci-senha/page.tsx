import type { Metadata } from "next";
import Link from "next/link";
import { ActionForm, Field } from "@/components/ui/action-form";
import { Input } from "@/components/ui/primitives";
import { forgotPasswordAction } from "@/lib/actions-senha";

export const metadata: Metadata = { title: "Esqueci minha senha" };

export default function EsqueciSenhaPage() {
  return (
    <div className="space-y-6">
      <div className="space-y-1.5">
        <h1 className="text-brand-institutional text-2xl font-bold">Esqueci minha senha</h1>
        <p className="text-muted-foreground text-sm">
          Enviamos um link para você criar uma senha nova. Ele vale por 30 minutos e só uma vez.
        </p>
      </div>
      <ActionForm action={forgotPasswordAction} submitLabel="Enviar link">
        <Field label="E-mail da sua conta" htmlFor="email">
          <Input id="email" name="email" type="email" autoComplete="username" required />
        </Field>
        <Field label="Empresa (opcional)" htmlFor="tenant">
          <Input id="tenant" name="tenant" placeholder="identificador da empresa" autoCapitalize="none" />
        </Field>
      </ActionForm>
      <p className="text-muted-foreground text-center text-sm">
        <Link href="/entrar" className="text-brand-institutional font-medium underline">
          Voltar para o login
        </Link>
      </p>
    </div>
  );
}
