import type { Metadata } from "next";
import Link from "next/link";
import { ActionForm, Field } from "@/components/ui/action-form";
import { Input } from "@/components/ui/primitives";
import { resetPasswordAction } from "@/lib/actions-senha";
import { API_URL } from "@/lib/session";

export const metadata: Metadata = { title: "Criar senha", robots: { index: false } };

type TokenInfo = { purpose: "redefinir" | "convite"; email: string; full_name: string; expires_at: string };

async function lookup(token: string): Promise<TokenInfo | null> {
  if (!token) return null;
  try {
    const res = await fetch(`${API_URL}/api/v1/auth/password/token?${new URLSearchParams({ token })}`, {
      cache: "no-store",
    });
    return res.ok ? ((await res.json()) as TokenInfo) : null;
  } catch {
    return null;
  }
}

export default async function RedefinirSenhaPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const { token = "" } = await searchParams;
  const info = await lookup(token);

  if (!info) {
    return (
      <div className="space-y-4">
        <h1 className="text-brand-institutional text-2xl font-bold">Link inválido ou expirado</h1>
        <p className="text-muted-foreground text-sm">
          Os links valem uma vez só e por pouco tempo. Peça um novo — ou, se era um convite, peça ao
          administrador para convidar de novo.
        </p>
        <Link href="/esqueci-senha" className="text-brand-institutional text-sm font-medium underline">
          Pedir um link novo
        </Link>
      </div>
    );
  }

  const invite = info.purpose === "convite";
  return (
    <div className="space-y-6">
      <div className="space-y-1.5">
        <h1 className="text-brand-institutional text-2xl font-bold">
          {invite ? "Crie sua senha" : "Senha nova"}
        </h1>
        <p className="text-muted-foreground text-sm">
          {info.full_name} · {info.email}
        </p>
      </div>
      <ActionForm
        action={resetPasswordAction.bind(null, token)}
        submitLabel={invite ? "Criar senha" : "Salvar senha"}
      >
        {/* ajuda gerenciadores de senha a guardar para o e-mail certo */}
        <input
          type="email"
          name="username"
          autoComplete="username"
          defaultValue={info.email}
          hidden
          readOnly
        />
        <Field label="Senha nova (mín. 12 caracteres)" htmlFor="new_password">
          <Input
            id="new_password"
            name="new_password"
            type="password"
            autoComplete="new-password"
            minLength={12}
            required
          />
        </Field>
        <Field label="Repita a senha" htmlFor="confirm">
          <Input
            id="confirm"
            name="confirm"
            type="password"
            autoComplete="new-password"
            minLength={12}
            required
          />
        </Field>
        <p className="text-muted-foreground text-xs">
          Dica: uma frase com quatro ou mais palavras é longa e fácil de lembrar.
          {!invite && " As sessões abertas em outros aparelhos serão encerradas."}
        </p>
      </ActionForm>
    </div>
  );
}
