"use server";

import { revalidatePath } from "next/cache";
import type { FormState } from "@/lib/actions-fase1";
import { parseDecimalBR } from "@/lib/format";
import { api, ApiError } from "@/lib/session";

const str = (f: FormData, k: string) => String(f.get(k) ?? "").trim();

async function guard(fn: () => Promise<FormState>): Promise<FormState> {
  try {
    return await fn();
  } catch (e) {
    if (e instanceof ApiError) return { error: e.message };
    throw e;
  }
}

function reasonOf(f: FormData): string {
  const reason = str(f, "reason");
  if (reason.length < 3) throw new ApiError(422, "Informe o motivo (mín. 3 caracteres).");
  return reason;
}

function refresh(contractId: string, lotId: string) {
  revalidatePath(`/app/contratos/${contractId}`);
  revalidatePath(`/app/lotes/${lotId}`);
  revalidatePath("/app/financeiro/receber");
}

export async function renegotiateAction(
  contractId: string,
  lotId: string,
  _p: FormState,
  f: FormData,
): Promise<FormState> {
  return guard(async () => {
    const n = Number(str(f, "installments_count"));
    if (!Number.isInteger(n) || n < 1) throw new ApiError(422, "Número de parcelas inválido");
    const discount = parseDecimalBR(str(f, "discount") || "0");
    if (discount === null) throw new ApiError(422, "Desconto inválido");
    const interestRaw = str(f, "interest_monthly_pct");
    const interest = interestRaw ? parseDecimalBR(interestRaw, 4) : null;
    if (interestRaw && interest === null) throw new ApiError(422, "Juros inválido");
    await api(`/contracts/${contractId}/renegotiations`, {
      method: "POST",
      body: JSON.stringify({
        installments_count: n,
        interest_monthly_pct: interest,
        amortization: str(f, "amortization") || null,
        first_due_date: str(f, "first_due_date"),
        include_late_charges: f.get("include_late_charges") === "on",
        discount,
        reason: reasonOf(f),
      }),
    });
    refresh(contractId, lotId);
    return { ok: "Saldo renegociado — novas parcelas geradas" };
  });
}

export async function rescindAction(
  contractId: string,
  lotId: string,
  _p: FormState,
  f: FormData,
): Promise<FormState> {
  return guard(async () => {
    const pct = parseDecimalBR(str(f, "retention_pct") || "10");
    if (pct === null || Number(pct) > 100) throw new ApiError(422, "Retenção inválida (0 a 100%)");
    if (f.get("confirm") !== "on") throw new ApiError(422, "Confirme que o distrato não pode ser desfeito.");
    await api(`/contracts/${contractId}/rescind`, {
      method: "POST",
      body: JSON.stringify({
        retention_pct: pct,
        refund_due_date: str(f, "refund_due_date") || null,
        reason: reasonOf(f),
      }),
    });
    refresh(contractId, lotId);
    revalidatePath("/app/financeiro/pagar");
    return { ok: "Distrato registrado" };
  });
}
