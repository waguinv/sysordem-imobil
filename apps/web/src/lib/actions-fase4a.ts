"use server";

import { revalidatePath } from "next/cache";
import type { FormState } from "@/lib/actions-fase1";
import { parseDecimalBR } from "@/lib/format";
import { api, ApiError } from "@/lib/session";
import type { ChargeDetail } from "@/lib/types";

const str = (f: FormData, k: string) => String(f.get(k) ?? "").trim();

async function guard(fn: () => Promise<FormState>): Promise<FormState> {
  try {
    return await fn();
  } catch (e) {
    if (e instanceof ApiError) return { error: e.message };
    throw e;
  }
}

function refresh(c: ChargeDetail) {
  revalidatePath(`/app/financeiro/cobrancas/${c.id}`);
  revalidatePath("/app/financeiro/cobrancas");
  revalidatePath(`/app/financeiro/parcelas/${c.installment_id}`);
  revalidatePath(`/app/contratos/${c.contract_id}`);
  revalidatePath("/app/financeiro/receber");
}

export async function issueChargeAction(
  installmentId: string,
  _p: FormState,
  f: FormData,
): Promise<FormState> {
  return guard(async () => {
    const c = await api<ChargeDetail>(`/installments/${installmentId}/charges`, {
      method: "POST",
      body: JSON.stringify({ method: str(f, "method") || "pix", due_date: str(f, "due_date") || null }),
    });
    refresh(c);
    return { ok: c.method === "pix" ? "Cobrança Pix emitida" : "Boleto emitido" };
  });
}

export async function cancelChargeAction(chargeId: string, _p: FormState, f: FormData): Promise<FormState> {
  const reason = str(f, "reason");
  if (reason.length < 3) return { error: "Informe o motivo (mín. 3 caracteres)." };
  return guard(async () => {
    const c = await api<ChargeDetail>(`/charges/${chargeId}/cancel`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
    refresh(c);
    return { ok: "Cobrança cancelada" };
  });
}

export async function resolveChargeAction(chargeId: string, _p: FormState, f: FormData): Promise<FormState> {
  const reason = str(f, "reason");
  if (reason.length < 3) return { error: "Descreva como a divergência foi resolvida (mín. 3 caracteres)." };
  return guard(async () => {
    const c = await api<ChargeDetail>(`/charges/${chargeId}/resolve`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
    refresh(c);
    return { ok: "Divergência marcada como resolvida" };
  });
}

const RESULT_MSG: Record<string, string> = {
  processado: "Webhook processado",
  divergente: "Pagamento confirmado, mas ficou divergente — veja o motivo",
  ignorado: "Webhook ignorado",
  duplicado: "Webhook duplicado (sem efeito)",
};

export async function simulateChargeAction(chargeId: string, _p: FormState, f: FormData): Promise<FormState> {
  return guard(async () => {
    const event = str(f, "event") === "expired" ? "expired" : "paid";
    const rawAmount = str(f, "amount");
    const amount = rawAmount ? parseDecimalBR(rawAmount) : null;
    if (rawAmount && (!amount || Number(amount) <= 0)) throw new ApiError(422, "Valor inválido");
    const out = await api<{ result: string; detail: string | null; charge: ChargeDetail }>(
      `/charges/${chargeId}/sandbox/simulate`,
      {
        method: "POST",
        body: JSON.stringify({ event, amount, paid_on: str(f, "paid_on") || null }),
      },
    );
    refresh(out.charge);
    const msg = RESULT_MSG[out.result] ?? out.result;
    return out.result === "divergente" || out.result === "ignorado"
      ? { error: `${msg}${out.detail ? `: ${out.detail}` : ""}` }
      : { ok: msg };
  });
}
