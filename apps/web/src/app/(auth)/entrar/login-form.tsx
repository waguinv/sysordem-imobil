"use client";

import { Loader2 } from "lucide-react";
import { useActionState } from "react";
import { Button, Input, Label } from "@/components/ui/primitives";
import { loginAction, type LoginState } from "@/lib/actions";

export function LoginForm() {
  const [state, action, pending] = useActionState<LoginState, FormData>(loginAction, {});
  return (
    <form action={action} className="space-y-4" noValidate>
      <div className="space-y-2">
        <Label htmlFor="tenant">Empresa</Label>
        <Input
          id="tenant"
          name="tenant"
          placeholder="identificador da empresa"
          autoComplete="organization"
          autoCapitalize="none"
          defaultValue={state.values?.tenant}
          required
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="email">E-mail</Label>
        <Input
          id="email"
          name="email"
          type="email"
          autoComplete="username"
          defaultValue={state.values?.email}
          required
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="password">Senha</Label>
        <Input id="password" name="password" type="password" autoComplete="current-password" required />
      </div>
      {state.error && (
        <p role="alert" className="text-destructive text-sm">
          {state.error}
        </p>
      )}
      <Button type="submit" className="w-full" size="lg" disabled={pending}>
        {pending && <Loader2 className="animate-spin" />} Entrar
      </Button>
    </form>
  );
}
