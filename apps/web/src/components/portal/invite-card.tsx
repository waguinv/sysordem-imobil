"use client";

import { Loader2 } from "lucide-react";
import { useActionState } from "react";
import { Field, selectClass } from "@/components/ui/action-form";
import { CopyButton } from "@/components/ui/copy-button";
import { Button, Input } from "@/components/ui/primitives";
import { inviteToPortalAction, type InviteState } from "@/lib/actions-portal";

/** Convite ao portal do comprador: o link aparece uma única vez (o servidor guarda só o hash). */
export function PortalInviteForm({
  customers,
}: {
  customers: { id: string; name: string; email: string | null }[];
}) {
  const [state, action, pending] = useActionState<InviteState, FormData>(inviteToPortalAction, {});
  return (
    <form action={action} className="space-y-3">
      <Field label="Comprador" htmlFor="pi-customer">
        <select id="pi-customer" name="customer_id" className={selectClass} required>
          {customers.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
              {c.email ? ` (${c.email})` : " — sem e-mail"}
            </option>
          ))}
        </select>
      </Field>
      <Field label="E-mail de acesso (vazio = o do cadastro)" htmlFor="pi-email">
        <Input id="pi-email" name="email" type="email" maxLength={254} />
      </Field>
      {state.error && (
        <p role="alert" className="text-destructive text-sm">
          {state.error}
        </p>
      )}
      {state.link && !pending && (
        <div role="status" className="bg-brand-surface/60 space-y-2 rounded-lg p-3 text-sm">
          <p className="text-brand-institutional">{state.ok}</p>
          <p className="font-mono text-xs break-all select-all">{state.link}</p>
          <CopyButton text={state.link} label="Copiar link" />
          {state.expires && (
            <p className="text-muted-foreground text-xs">
              Vale até{" "}
              {new Date(state.expires).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" })}.
            </p>
          )}
        </div>
      )}
      <Button type="submit" disabled={pending}>
        {pending && <Loader2 className="animate-spin" />} Gerar convite
      </Button>
    </form>
  );
}
