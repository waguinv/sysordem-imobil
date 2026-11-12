"use client";

import { useActionState } from "react";
import { toggleBrokerAction } from "@/lib/actions-fase2a";
import type { FormState } from "@/lib/actions-fase1";

export function BrokerToggle({ brokerId, active }: { brokerId: string; active: boolean }) {
  const [state, action, pending] = useActionState<FormState>(
    toggleBrokerAction.bind(null, brokerId, !active),
    {},
  );
  return (
    <form action={action} className="flex items-center gap-2">
      {state.error && <span className="text-destructive text-xs">{state.error}</span>}
      <button
        type="submit"
        disabled={pending}
        className="text-brand-institutional text-xs font-medium hover:underline disabled:opacity-50"
      >
        {active ? "Desativar" : "Reativar"}
      </button>
    </form>
  );
}
