"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import type { FormState } from "@/lib/actions-fase1";
import { parseDecimalBR } from "@/lib/format";
import { api, ApiError } from "@/lib/session";
import type { LeadDetail, Proposal } from "@/lib/types";

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

// ─────────────────────────── leads ───────────────────────────

export async function createLeadAction(_p: FormState, f: FormData): Promise<FormState> {
  let id: string | undefined;
  const res = await guard(async () => {
    const lead = await api<LeadDetail>("/leads", {
      method: "POST",
      body: JSON.stringify({
        name: str(f, "name"),
        email: opt(f, "email"),
        phone: opt(f, "phone"),
        source: str(f, "source") || "outro",
        development_id: opt(f, "development_id"),
        broker_id: opt(f, "broker_id"),
        notes: opt(f, "notes"),
      }),
    });
    id = lead.id;
    return { ok: "Lead cadastrado" };
  });
  if (!id) return res;
  redirect(`/app/leads/${id}`);
}

export async function addActivityAction(leadId: string, _p: FormState, f: FormData): Promise<FormState> {
  return guard(async () => {
    await api(`/leads/${leadId}/activities`, {
      method: "POST",
      body: JSON.stringify({ kind: str(f, "kind") || "nota", description: str(f, "description") }),
    });
    revalidatePath(`/app/leads/${leadId}`);
    return { ok: "Registrado" };
  });
}

export async function changeLeadStatusAction(leadId: string, _p: FormState, f: FormData): Promise<FormState> {
  const status = str(f, "status");
  return guard(async () => {
    await api(`/leads/${leadId}`, {
      method: "PATCH",
      body: JSON.stringify({ status, lost_reason: status === "perdido" ? opt(f, "lost_reason") : null }),
    });
    revalidatePath(`/app/leads/${leadId}`);
    return { ok: "Situação atualizada" };
  });
}

export async function reassignLeadAction(leadId: string, _p: FormState, f: FormData): Promise<FormState> {
  return guard(async () => {
    await api(`/leads/${leadId}`, {
      method: "PATCH",
      body: JSON.stringify({ broker_id: opt(f, "broker_id") }),
    });
    revalidatePath(`/app/leads/${leadId}`);
    return { ok: "Corretor atualizado" };
  });
}

export async function convertLeadAction(leadId: string, _p: FormState, f: FormData): Promise<FormState> {
  return guard(async () => {
    const existing = opt(f, "customer_id");
    await api(`/leads/${leadId}/convert`, {
      method: "POST",
      body: JSON.stringify(
        existing
          ? { customer_id: existing }
          : { document: opt(f, "document"), city: opt(f, "city"), state: opt(f, "state") },
      ),
    });
    revalidatePath(`/app/leads/${leadId}`);
    return { ok: "Lead convertido em comprador" };
  });
}

// ─────────────────────────── propostas ───────────────────────────

function money(f: FormData, k: string, label: string, optional = false): string | null {
  const raw = str(f, k);
  if (!raw && optional) return null;
  const v = parseDecimalBR(raw || "0");
  if (v === null) throw new ApiError(422, `${label} inválido`);
  return v;
}

export async function createProposalAction(lotId: string, _p: FormState, f: FormData): Promise<FormState> {
  let id: string | undefined;
  const res = await guard(async () => {
    const n = Number(str(f, "installments_count") || "0");
    if (!Number.isInteger(n) || n < 0 || n > 480)
      throw new ApiError(422, "Número de parcelas inválido (0 a 480)");
    const interestRaw = str(f, "interest_monthly_pct");
    const interest = interestRaw ? parseDecimalBR(interestRaw, 4) : null;
    if (interestRaw && interest === null) throw new ApiError(422, "Juros inválido");
    const p = await api<Proposal>("/proposals", {
      method: "POST",
      body: JSON.stringify({
        lot_id: lotId,
        customer_id: str(f, "customer_id"),
        broker_id: opt(f, "broker_id"),
        lead_id: opt(f, "lead_id"),
        valid_until: opt(f, "valid_until"),
        total_price: money(f, "total_price", "Valor proposto", true),
        down_payment: money(f, "down_payment", "Entrada"),
        installments_count: n,
        interest_monthly_pct: interest,
        amortization: opt(f, "amortization"),
        first_due_date: n > 0 ? opt(f, "first_due_date") : null,
        notes: opt(f, "notes"),
      }),
    });
    id = p.id;
    return { ok: "Proposta registrada" };
  });
  if (!id) return res;
  redirect(`/app/propostas/${id}`);
}

export async function decideProposalAction(
  proposalId: string,
  kind: "accept" | "reject" | "cancel",
  _p: FormState,
  f: FormData,
): Promise<FormState> {
  return guard(async () => {
    const body =
      kind === "accept"
        ? { hours: Number(str(f, "hours")) || null, reason: str(f, "reason") || "proposta aceita" }
        : { reason: reasonOf(f) };
    const p = await api<Proposal>(`/proposals/${proposalId}/${kind}`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    revalidatePath(`/app/propostas/${proposalId}`);
    revalidatePath(`/app/lotes/${p.lot_id}`);
    return {
      ok:
        kind === "accept"
          ? "Proposta aceita e lote reservado"
          : kind === "reject"
            ? "Proposta recusada"
            : "Proposta cancelada",
    };
  });
}

// ─────────────────────────── corretor ↔ usuário ───────────────────────────

export async function linkBrokerUserAction(_p: FormState, f: FormData): Promise<FormState> {
  const brokerId = str(f, "broker_id");
  if (!brokerId) return { error: "Escolha o corretor." };
  return guard(async () => {
    await api(`/brokers/${brokerId}`, {
      method: "PATCH",
      body: JSON.stringify({ user_id: opt(f, "user_id"), reason: "acesso do corretor ao sistema" }),
    });
    revalidatePath("/app/corretores");
    return { ok: "Acesso atualizado" };
  });
}
