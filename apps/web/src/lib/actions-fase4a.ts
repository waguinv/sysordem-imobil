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
    const raw = str(f, "event");
    const event = ["expired", "refunded", "partially_refunded"].includes(raw) ? raw : "paid";
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

const SYNC_MSG: Record<string, string> = {
  sem_alteracao: "Nada mudou no Asaas: a cobrança segue pendente",
  processado: "Situação atualizada pelo Asaas",
  divergente: "Pagamento confirmado no Asaas, mas ficou divergente — veja o motivo",
  ignorado: "Nada a atualizar",
  duplicado: "Já estava atualizada",
};

export async function syncChargeAction(chargeId: string, _p: FormState, _f: FormData): Promise<FormState> {
  return guard(async () => {
    const out = await api<{ result: string; detail: string | null; charge: ChargeDetail }>(
      `/charges/${chargeId}/sync`,
      { method: "POST" },
    );
    refresh(out.charge);
    const msg = SYNC_MSG[out.result] ?? out.result;
    return out.result === "divergente"
      ? { error: `${msg}${out.detail ? `: ${out.detail}` : ""}` }
      : { ok: msg };
  });
}

export async function connectPaymentAccountAction(_p: FormState, f: FormData): Promise<FormState> {
  const apiKey = str(f, "api_key");
  const environment = str(f, "environment") === "production" ? "production" : "sandbox";
  if (apiKey.length < 20) return { error: "Cole a chave de API completa do Asaas." };
  return guard(async () => {
    await api("/settings/payments", {
      method: "PUT",
      body: JSON.stringify({ api_key: apiKey, environment }),
    });
    revalidatePath("/app/configuracoes/recebimentos");
    return { ok: "Conta Asaas ligada" };
  });
}

export async function disconnectPaymentAccountAction(_p: FormState, f: FormData): Promise<FormState> {
  const reason = str(f, "reason");
  if (reason.length < 3) return { error: "Informe o motivo (mín. 3 caracteres)." };
  return guard(async () => {
    await api("/settings/payments/disconnect", { method: "POST", body: JSON.stringify({ reason }) });
    revalidatePath("/app/configuracoes/recebimentos");
    return { ok: "Conta Asaas desligada" };
  });
}

export async function saveCompanyDocumentAction(_p: FormState, f: FormData): Promise<FormState> {
  const document = str(f, "document").replace(/\D/g, "");
  if (document.length !== 14 && document.length !== 11) return { error: "Informe o CNPJ (14 dígitos)." };
  return guard(async () => {
    await api("/settings/company-document", { method: "PUT", body: JSON.stringify({ document }) });
    revalidatePath("/app/configuracoes/recebimentos");
    revalidatePath("/app/configuracoes/assinatura");
    return { ok: "CNPJ salvo" };
  });
}
