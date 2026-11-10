"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import type { FormState } from "@/lib/actions-fase1";
import { parseDecimalBR } from "@/lib/format";
import { api, ApiError } from "@/lib/session";
import type { ContractDetail } from "@/lib/types";

const str = (f: FormData, k: string) => String(f.get(k) ?? "").trim();

function num(f: FormData, k: string, label: string, decimals = 2, optional = false): string | null {
  const raw = str(f, k);
  if (!raw && optional) return null;
  const v = parseDecimalBR(raw || "0", decimals);
  if (v === null) throw new ApiError(422, `${label} inválido`);
  return v;
}

/** Lê as condições do formulário (TermsFields). */
function terms(f: FormData) {
  const n = Number(str(f, "installments_count") || "0");
  if (!Number.isInteger(n) || n < 0 || n > 480)
    throw new ApiError(422, "Número de parcelas inválido (0 a 480)");
  return {
    total_price: num(f, "total_price", "Valor total", 2, true),
    down_payment: num(f, "down_payment", "Entrada"),
    installments_count: n,
    interest_monthly_pct: num(f, "interest_monthly_pct", "Juros", 4, true),
    amortization: str(f, "amortization") || null,
    first_due_date: n > 0 ? str(f, "first_due_date") || null : null,
    adjustment_index: str(f, "adjustment_index") || null,
    // ausentes quando a política não permite alterar: o servidor aplica os da empresa
    late_fee_pct: num(f, "late_fee_pct", "Multa", 2, true),
    late_interest_monthly_pct: num(f, "late_interest_monthly_pct", "Juros de mora", 2, true),
    notes: str(f, "notes") || null,
  };
}

async function guard(fn: () => Promise<FormState>): Promise<FormState> {
  try {
    return await fn();
  } catch (e) {
    if (e instanceof ApiError) return { error: e.message };
    throw e;
  }
}

export async function createContractAction(
  reservationId: string,
  lotId: string,
  _p: FormState,
  f: FormData,
): Promise<FormState> {
  let id: string | undefined;
  const res = await guard(async () => {
    const c = await api<ContractDetail>("/contracts", {
      method: "POST",
      body: JSON.stringify({ reservation_id: reservationId, ...terms(f) }),
    });
    id = c.id;
    return { ok: "Contrato gerado" };
  });
  if (!id) return res;
  // Sem revalidatePath da página atual: combinado com redirect, deixava a navegação pendente.
  // As telas de lote/contrato são dinâmicas (sem cache), então mostram o estado novo ao abrir.
  void lotId;
  redirect(`/app/contratos/${id}`);
}

export async function newVersionAction(
  contractId: string,
  version: number,
  _p: FormState,
  f: FormData,
): Promise<FormState> {
  const reason = str(f, "reason");
  if (reason.length < 3) return { error: "Informe o motivo da alteração (mín. 3 caracteres)." };
  return guard(async () => {
    await api(`/contracts/${contractId}/versions`, {
      method: "POST",
      body: JSON.stringify({ ...terms(f), expected_version: version, reason }),
    });
    revalidatePath(`/app/contratos/${contractId}`);
    return { ok: "Nova versão salva" };
  });
}

export async function signContractAction(
  contractId: string,
  version: number,
  _p: FormState,
): Promise<FormState> {
  return guard(async () => {
    await api(`/contracts/${contractId}/sign`, {
      method: "POST",
      body: JSON.stringify({ expected_version: version }),
    });
    revalidatePath(`/app/contratos/${contractId}`);
    return { ok: "Assinatura registrada" };
  });
}

export async function cancelContractAction(
  contractId: string,
  _p: FormState,
  f: FormData,
): Promise<FormState> {
  const reason = str(f, "reason");
  if (reason.length < 3) return { error: "Informe o motivo (mín. 3 caracteres)." };
  return guard(async () => {
    await api(`/contracts/${contractId}/cancel`, { method: "POST", body: JSON.stringify({ reason }) });
    revalidatePath(`/app/contratos/${contractId}`);
    return { ok: "Contrato cancelado" };
  });
}

export async function savePolicyAction(_p: FormState, f: FormData): Promise<FormState> {
  const reason = str(f, "reason");
  if (reason.length < 3) return { error: "Informe o motivo da alteração (mín. 3 caracteres)." };
  const allowed = f.getAll("allowed_amortizations").map(String);
  return guard(async () => {
    await api("/settings/contract-policy", {
      method: "PUT",
      body: JSON.stringify({
        default_interest_monthly_pct: num(f, "default_interest_monthly_pct", "Juros padrão", 4),
        max_interest_monthly_pct: num(f, "max_interest_monthly_pct", "Juros máximo", 4),
        default_amortization: str(f, "default_amortization"),
        allowed_amortizations: allowed,
        default_adjustment_index: str(f, "default_adjustment_index"),
        late_fee_pct: num(f, "late_fee_pct", "Multa"),
        late_interest_monthly_pct: num(f, "late_interest_monthly_pct", "Juros de mora"),
        max_installments: Number(str(f, "max_installments") || "0"),
        min_down_payment_pct: num(f, "min_down_payment_pct", "Entrada mínima"),
        allow_override_late_charges: f.get("allow_override_late_charges") === "on",
        reason,
      }),
    });
    revalidatePath("/app/configuracoes/politica-contratos");
    return { ok: "Política salva. Vale para novos contratos e novas versões." };
  });
}
