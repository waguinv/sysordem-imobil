"use server";

import { revalidatePath } from "next/cache";
import type { FormState } from "@/lib/actions-fase1";
import { api, ApiError } from "@/lib/session";

const str = (f: FormData, k: string) => String(f.get(k) ?? "").trim();

async function run(fn: () => Promise<unknown>, ok: string, contractId: string): Promise<FormState> {
  try {
    await fn();
  } catch (e) {
    if (e instanceof ApiError) return { error: e.message };
    throw e;
  }
  revalidatePath(`/app/contratos/${contractId}`);
  return { ok };
}

export async function sendForSignatureAction(
  contractId: string,
  version: number,
  _p: FormState,
  f: FormData,
): Promise<FormState> {
  return run(
    () =>
      api(`/contracts/${contractId}/signature-requests`, {
        method: "POST",
        body: JSON.stringify({
          expected_version: version,
          provider: str(f, "provider") || null,
          message: str(f, "message") || null,
        }),
      }),
    "Enviado para assinatura. Os signatários recebem o link por e-mail.",
    contractId,
  );
}

export async function cancelSignatureAction(
  reqId: string,
  contractId: string,
  _p: FormState,
  f: FormData,
): Promise<FormState> {
  const reason = str(f, "reason");
  if (reason.length < 3) return { error: "Informe o motivo (mín. 3 caracteres)." };
  return run(
    () => api(`/signature-requests/${reqId}/cancel`, { method: "POST", body: JSON.stringify({ reason }) }),
    "Envio cancelado.",
    contractId,
  );
}

export async function simulateSignatureAction(
  reqId: string,
  contractId: string,
  action: "sign_all" | "reject",
  _p: FormState,
): Promise<FormState> {
  return run(
    () =>
      api(`/signature-requests/${reqId}/sandbox/simulate`, {
        method: "POST",
        body: JSON.stringify({ action, reason: action === "reject" ? "recusa simulada" : null }),
      }),
    action === "sign_all" ? "Simulação: todos assinaram." : "Simulação: assinatura recusada.",
    contractId,
  );
}
