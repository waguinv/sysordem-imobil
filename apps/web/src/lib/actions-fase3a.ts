"use server";

import { revalidatePath } from "next/cache";
import type { FormState } from "@/lib/actions-fase1";
import { parseDecimalBR } from "@/lib/format";
import { api, ApiError } from "@/lib/session";
import type { InstallmentDetail } from "@/lib/types";

const str = (f: FormData, k: string) => String(f.get(k) ?? "").trim();

async function guard(fn: () => Promise<FormState>): Promise<FormState> {
  try {
    return await fn();
  } catch (e) {
    if (e instanceof ApiError) return { error: e.message };
    throw e;
  }
}

function refresh(d: InstallmentDetail) {
  revalidatePath(`/app/financeiro/parcelas/${d.id}`);
  revalidatePath(`/app/contratos/${d.contract_id}`);
  revalidatePath("/app/financeiro/receber");
}

export async function registerPaymentAction(
  installmentId: string,
  _p: FormState,
  f: FormData,
): Promise<FormState> {
  return guard(async () => {
    const amount = parseDecimalBR(str(f, "amount"));
    if (!amount || Number(amount) <= 0) throw new ApiError(422, "Informe o valor recebido");
    const d = await api<InstallmentDetail>(`/installments/${installmentId}/payments`, {
      method: "POST",
      body: JSON.stringify({
        amount,
        received_on: str(f, "received_on") || null,
        method: str(f, "method") || "pix",
        notes: str(f, "notes") || null,
      }),
    });
    refresh(d);
    return {
      ok: d.status === "paga" ? "Recebimento registrado — parcela quitada" : "Recebimento parcial registrado",
    };
  });
}

export async function reversePaymentAction(
  paymentId: string,
  _p: FormState,
  f: FormData,
): Promise<FormState> {
  const reason = str(f, "reason");
  if (reason.length < 3) return { error: "Informe o motivo do estorno (mín. 3 caracteres)." };
  return guard(async () => {
    const d = await api<InstallmentDetail>(`/payments/${paymentId}/reverse`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
    refresh(d);
    return { ok: "Recebimento estornado" };
  });
}
