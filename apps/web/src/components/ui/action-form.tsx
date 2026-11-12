"use client";

import { Loader2 } from "lucide-react";
import { useActionState, useEffect, useRef } from "react";
import { Button } from "@/components/ui/primitives";
import type { FormState } from "@/lib/actions-fase1";
import { cn } from "@/lib/utils";

type Props = {
  action: (prev: FormState, form: FormData) => Promise<FormState>;
  submitLabel: string;
  className?: string;
  /** limpa os campos depois de salvar com sucesso */
  resetOnSuccess?: boolean;
  submitVariant?: "default" | "outline" | "destructive" | "institutional";
  children: React.ReactNode;
};

/** Formulário com Server Action, estado de envio e mensagem de erro/sucesso no padrão SysOrdem. */
export function ActionForm({
  action,
  submitLabel,
  className,
  resetOnSuccess,
  submitVariant,
  children,
}: Props) {
  const [state, formAction, pending] = useActionState<FormState, FormData>(action, {});
  const ref = useRef<HTMLFormElement>(null);
  useEffect(() => {
    if (state.ok && resetOnSuccess) ref.current?.reset();
  }, [state, resetOnSuccess]);
  return (
    <form ref={ref} action={formAction} className={cn("space-y-3", className)}>
      {children}
      {state.error && (
        <p role="alert" className="text-destructive text-sm">
          {state.error}
        </p>
      )}
      {state.ok && !pending && (
        <p role="status" className="text-brand-institutional text-sm">
          {state.ok}
        </p>
      )}
      <Button type="submit" disabled={pending} variant={submitVariant}>
        {pending && <Loader2 className="animate-spin" />} {submitLabel}
      </Button>
    </form>
  );
}

export function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="text-sm leading-none font-medium">
        {label}
      </label>
      {children}
    </div>
  );
}

export const selectClass =
  "border-input flex h-10 w-full rounded-md border bg-white px-3 text-base shadow-xs outline-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] md:text-sm";
