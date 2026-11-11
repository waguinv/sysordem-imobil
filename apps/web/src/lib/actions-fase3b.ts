"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import type { FormState } from "@/lib/actions-fase1";
import { parseDecimalBR } from "@/lib/format";
import { api, ApiError } from "@/lib/session";
import type { PayableDetail } from "@/lib/types";

const str = (f: FormData, k: string) => String(f.get(k) ?? "").trim();
const opt = (f: FormData, k: string) => str(f, k) || null;

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

function pctOf(f: FormData, k: string, label: string): string {
  const v = parseDecimalBR(str(f, k) || "0");
  if (v === null || Number(v) > 100) throw new ApiError(422, `${label} inválido (0 a 100)`);
  return v;
}

// ─────────────────────────── regras ───────────────────────────

export async function saveFinanceSettingsAction(_p: FormState, f: FormData): Promise<FormState> {
  return guard(async () => {
    await api("/settings/finance", {
      method: "PUT",
      body: JSON.stringify({
        commission_basis: str(f, "commission_basis"),
        default_commission_pct: pctOf(f, "default_commission_pct", "Percentual"),
        reason: reasonOf(f),
      }),
    });
    revalidatePath("/app/configuracoes/financeiro");
    return { ok: "Regras salvas. Valem para contratos assinados a partir de agora." };
  });
}

export async function saveOwnerShareAction(
  developmentId: string,
  _p: FormState,
  f: FormData,
): Promise<FormState> {
  return guard(async () => {
    await api(`/developments/${developmentId}/owner-share`, {
      method: "PUT",
      body: JSON.stringify({
        owner_share_pct: pctOf(f, "owner_share_pct", "Percentual"),
        reason: reasonOf(f),
      }),
    });
    revalidatePath("/app/configuracoes/financeiro");
    return { ok: "Repasse salvo" };
  });
}

export async function setBrokerCommissionAction(_p: FormState, f: FormData): Promise<FormState> {
  const brokerId = str(f, "broker_id");
  if (!brokerId) return { error: "Escolha o corretor." };
  return guard(async () => {
    const raw = str(f, "commission_pct");
    await api(`/brokers/${brokerId}`, {
      method: "PATCH",
      body: JSON.stringify({
        commission_pct: raw ? pctOf(f, "commission_pct", "Percentual") : null,
        reason: "percentual de comissão",
      }),
    });
    revalidatePath("/app/corretores");
    return { ok: "Comissão do corretor atualizada" };
  });
}

// ─────────────────────────── acerto ───────────────────────────

export async function settleAction(
  kind: "comissao" | "proprietario",
  beneficiaryId: string,
  _p: FormState,
  f: FormData,
): Promise<FormState> {
  let id: string | undefined;
  const res = await guard(async () => {
    const p = await api<PayableDetail>("/participations/settle", {
      method: "POST",
      body: JSON.stringify({ kind, beneficiary_id: beneficiaryId, due_date: opt(f, "due_date") }),
    });
    id = p.id;
    return { ok: "Conta a pagar gerada" };
  });
  if (!id) return res;
  redirect(`/app/financeiro/pagar/${id}`);
}

// ─────────────────────────── contas a pagar ───────────────────────────

export async function createPayableAction(_p: FormState, f: FormData): Promise<FormState> {
  let id: string | undefined;
  const res = await guard(async () => {
    const amount = parseDecimalBR(str(f, "amount"));
    if (!amount || Number(amount) <= 0) throw new ApiError(422, "Informe o valor");
    const p = await api<PayableDetail>("/payables", {
      method: "POST",
      body: JSON.stringify({
        description: str(f, "description"),
        category: str(f, "category"),
        payee_name: str(f, "payee_name"),
        development_id: opt(f, "development_id"),
        due_date: str(f, "due_date"),
        amount,
        notes: opt(f, "notes"),
      }),
    });
    id = p.id;
    return { ok: "Conta cadastrada" };
  });
  if (!id) return res;
  redirect(`/app/financeiro/pagar/${id}`);
}

export async function payableAction(
  payableId: string,
  kind: "pay" | "reverse" | "cancel",
  _p: FormState,
  f: FormData,
): Promise<FormState> {
  return guard(async () => {
    const body =
      kind === "pay"
        ? { paid_on: opt(f, "paid_on"), method: str(f, "method") || "pix", notes: opt(f, "notes") }
        : { reason: reasonOf(f) };
    await api(`/payables/${payableId}/${kind}`, { method: "POST", body: JSON.stringify(body) });
    revalidatePath(`/app/financeiro/pagar/${payableId}`);
    revalidatePath("/app/financeiro/pagar");
    revalidatePath("/app/comissoes");
    return {
      ok:
        kind === "pay"
          ? "Pagamento registrado"
          : kind === "reverse"
            ? "Pagamento estornado"
            : "Conta cancelada",
    };
  });
}
