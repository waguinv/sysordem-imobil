"use client";

import { Loader2 } from "lucide-react";
import Link from "next/link";
import { useActionState } from "react";
import { Button, Input, Label } from "@/components/ui/primitives";
import { signupAction, type SignupState } from "@/lib/actions-fase5c";
import type { PlanInfo } from "@/lib/billing";
import { limitLabel } from "@/lib/billing";
import { brl } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Sugere o identificador a partir do nome (sem acentos, minúsculo, com hífen). */
function slugify(s: string): string {
  return s
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
}

export function SignupForm({ plans }: { plans: PlanInfo[] }) {
  const [state, action, pending] = useActionState<SignupState, FormData>(signupAction, {});
  const v = state.values ?? {};
  return (
    <form action={action} className="space-y-4">
      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">Plano (14 dias grátis, sem cartão)</legend>
        <div className="grid gap-2">
          {plans.map((p) => (
            <label
              key={p.code}
              className={cn(
                "has-[:checked]:border-brand has-[:checked]:bg-brand/5 flex cursor-pointer items-start gap-3 rounded-lg border p-3 text-sm",
              )}
            >
              <input
                type="radio"
                name="plan_code"
                value={p.code}
                defaultChecked={(v.plan_code ?? "profissional") === p.code}
                className="mt-1"
              />
              <span className="flex-1">
                <span className="flex justify-between font-medium">
                  {p.name} <span className="tabular-nums">{brl(p.monthly_price)}/mês</span>
                </span>
                <span className="text-muted-foreground block text-xs">
                  {limitLabel(p.max_developments, "empreendimentos")} · {limitLabel(p.max_lots, "lotes")} ·{" "}
                  {limitLabel(p.max_users, "usuários")}
                </span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      <div className="space-y-2">
        <Label htmlFor="company_name">Nome da empresa</Label>
        <Input
          id="company_name"
          name="company_name"
          defaultValue={v.company_name}
          required
          minLength={2}
          maxLength={160}
          onBlur={(e) => {
            const slug = e.currentTarget.form?.elements.namedItem("slug") as HTMLInputElement | null;
            if (slug && !slug.value) slug.value = slugify(e.currentTarget.value);
          }}
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="slug">Identificador da empresa (usado para entrar)</Label>
        <Input
          id="slug"
          name="slug"
          defaultValue={v.slug}
          required
          minLength={3}
          maxLength={40}
          pattern="[a-z0-9][a-z0-9-]*[a-z0-9]"
          autoCapitalize="none"
          placeholder="ex.: loteadora-boa-vista"
        />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="full_name">Seu nome</Label>
          <Input id="full_name" name="full_name" defaultValue={v.full_name} required minLength={2} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="email">Seu e-mail</Label>
          <Input
            id="email"
            name="email"
            type="email"
            autoComplete="username"
            defaultValue={v.email}
            required
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="password">Senha</Label>
          <Input
            id="password"
            name="password"
            type="password"
            autoComplete="new-password"
            minLength={8}
            required
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="confirm">Repita a senha</Label>
          <Input
            id="confirm"
            name="confirm"
            type="password"
            autoComplete="new-password"
            minLength={8}
            required
          />
        </div>
      </div>
      {/* campo-isca: invisível para pessoas */}
      <input
        type="text"
        name="website"
        tabIndex={-1}
        autoComplete="off"
        className="hidden"
        aria-hidden="true"
      />
      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" name="accept_terms" required className="mt-1" /> Li e aceito os termos de uso e
        a política de privacidade.
      </label>
      {state.error && (
        <p role="alert" className="text-destructive text-sm">
          {state.error}
        </p>
      )}
      <Button type="submit" className="w-full" size="lg" disabled={pending}>
        {pending && <Loader2 className="animate-spin" />} Criar conta da empresa
      </Button>
      <p className="text-muted-foreground text-center text-sm">
        Já tem conta?{" "}
        <Link href="/entrar" className="text-brand-institutional font-medium underline">
          Entrar
        </Link>
      </p>
    </form>
  );
}
