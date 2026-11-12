"use server";

import { revalidatePath } from "next/cache";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import type { FormState } from "@/lib/actions-fase1";
import { api, API_URL, ApiError, SESSION_COOKIE } from "@/lib/session";

const str = (f: FormData, k: string) => String(f.get(k) ?? "").trim();

async function guard(fn: () => Promise<FormState>): Promise<FormState> {
  try {
    return await fn();
  } catch (e) {
    if (e instanceof ApiError) return { error: e.message };
    throw e;
  }
}

function refresh() {
  revalidatePath("/app/configuracoes/assinatura");
  revalidatePath("/app", "layout");
}

// ─────────────────────────── cadastro self-service ───────────────────────────

export type SignupState = FormState & { values?: Record<string, string> };

export async function signupAction(_p: SignupState, f: FormData): Promise<SignupState> {
  const values = {
    company_name: str(f, "company_name"),
    slug: str(f, "slug").toLowerCase(),
    full_name: str(f, "full_name"),
    email: str(f, "email"),
    plan_code: str(f, "plan_code") || "profissional",
  };
  const password = String(f.get("password") ?? "");
  if (password !== String(f.get("confirm") ?? "")) return { error: "As senhas não conferem.", values };
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/v1/signup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...values,
        password,
        accept_terms: f.get("accept_terms") === "on",
        website: str(f, "website") || null,
      }),
      cache: "no-store",
    });
  } catch {
    return { error: "Servidor indisponível. Tente novamente em instantes.", values };
  }
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
    const detail = Array.isArray(body.detail)
      ? "Confira os campos: e-mail válido e senha com pelo menos 8 caracteres."
      : typeof body.detail === "string"
        ? body.detail
        : "Não foi possível concluir o cadastro.";
    return { error: detail, values };
  }
  const { access_token, expires_in } = (await res.json()) as { access_token: string; expires_in: number };
  (await cookies()).set(SESSION_COOKIE, access_token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: expires_in,
  });
  redirect("/app?bem-vindo=1");
}

// ─────────────────────────── assinatura ───────────────────────────

export async function changePlanAction(planCode: string, _p: FormState): Promise<FormState> {
  return guard(async () => {
    await api("/billing/plan", { method: "POST", body: JSON.stringify({ plan_code: planCode }) });
    refresh();
    return { ok: "Plano alterado" };
  });
}

export async function payInvoiceAction(invoiceId: string, _p: FormState, f: FormData): Promise<FormState> {
  return guard(async () => {
    await api(`/billing/invoices/${invoiceId}/pay`, {
      method: "POST",
      body: JSON.stringify({ method: str(f, "method") || "pix" }),
    });
    refresh();
    return { ok: "Código gerado" };
  });
}

export async function simulateInvoiceAction(invoiceId: string, _p: FormState): Promise<FormState> {
  return guard(async () => {
    const r = await api<{ result: string }>(`/billing/invoices/${invoiceId}/sandbox/simulate`, {
      method: "POST",
    });
    refresh();
    return r.result === "pago"
      ? { ok: "Pagamento simulado e confirmado" }
      : { error: `Resultado: ${r.result}` };
  });
}

export async function cancelSubscriptionAction(_p: FormState, f: FormData): Promise<FormState> {
  const reason = str(f, "reason");
  if (reason.length < 3) return { error: "Informe o motivo (mín. 3 caracteres)." };
  if (f.get("confirm") !== "on") return { error: "Confirme o cancelamento." };
  return guard(async () => {
    await api("/billing/cancel", { method: "POST", body: JSON.stringify({ reason }) });
    refresh();
    return { ok: "Assinatura cancelada. O acesso continua até o fim do período pago." };
  });
}

export async function reactivateSubscriptionAction(_p: FormState): Promise<FormState> {
  return guard(async () => {
    await api("/billing/reactivate", { method: "POST" });
    refresh();
    return { ok: "Assinatura reativada" };
  });
}
