"use server";

import { revalidatePath } from "next/cache";
import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import type { FormState } from "@/lib/actions-fase1";
import { PORTAL_COOKIE, PortalError, portalApi } from "@/lib/portal";
import { api, API_URL, ApiError } from "@/lib/session";

const str = (f: FormData, k: string) => String(f.get(k) ?? "").trim();

async function setSession(token: string, expiresIn: number) {
  (await cookies()).set(PORTAL_COOKIE, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: expiresIn,
  });
}

async function post(path: string, body: unknown): Promise<Response | null> {
  try {
    return await fetch(`${API_URL}/api/v1${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    return null;
  }
}

// ─────────────────────────── comprador ───────────────────────────

export async function portalLoginAction(slug: string, _p: FormState, f: FormData): Promise<FormState> {
  const email = str(f, "email");
  const password = String(f.get("password") ?? "");
  if (!email || !password) return { error: "Informe e-mail e senha." };
  const res = await post("/portal/auth/login", { tenant_slug: slug, email, password });
  if (!res) return { error: "Servidor indisponível. Tente novamente em instantes." };
  if (res.status === 429) return { error: "Muitas tentativas. Aguarde alguns minutos." };
  if (!res.ok) return { error: "E-mail ou senha incorretos." };
  const { access_token, expires_in } = (await res.json()) as { access_token: string; expires_in: number };
  await setSession(access_token, expires_in);
  redirect(`/portal/${encodeURIComponent(slug)}`);
}

export async function portalActivateAction(
  slug: string,
  token: string,
  _p: FormState,
  f: FormData,
): Promise<FormState> {
  const password = String(f.get("password") ?? "");
  if (password !== String(f.get("confirm") ?? "")) return { error: "As senhas não conferem." };
  const res = await post("/portal/auth/activate", { tenant_slug: slug, token, password });
  if (!res) return { error: "Servidor indisponível. Tente novamente em instantes." };
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
    return { error: typeof body.detail === "string" ? body.detail : "Não foi possível ativar o acesso." };
  }
  const { access_token, expires_in } = (await res.json()) as { access_token: string; expires_in: number };
  await setSession(access_token, expires_in);
  redirect(`/portal/${encodeURIComponent(slug)}`);
}

export async function portalLogoutAction(slug: string): Promise<void> {
  (await cookies()).delete(PORTAL_COOKIE);
  redirect(`/portal/${encodeURIComponent(slug)}/entrar`);
}

export async function portalChargeAction(
  slug: string,
  contractId: string,
  installmentId: string,
  _p: FormState,
  f: FormData,
): Promise<FormState> {
  try {
    await portalApi(slug, `/installments/${installmentId}/charges`, {
      method: "POST",
      body: JSON.stringify({ method: str(f, "method") || "pix" }),
    });
  } catch (e) {
    if (e instanceof PortalError) return { error: e.message };
    throw e;
  }
  revalidatePath(`/portal/${slug}/contratos/${contractId}`);
  return { ok: "Código gerado" };
}

// ─────────────────────────── equipe: convite ───────────────────────────

export type InviteState = FormState & { link?: string; expires?: string };

export async function inviteToPortalAction(_p: InviteState, f: FormData): Promise<InviteState> {
  const customerId = str(f, "customer_id");
  if (!customerId) return { error: "Escolha o comprador." };
  try {
    const out = await api<{ invite_path: string; invite_expires_at: string | null }>(
      `/customers/${customerId}/portal-invite`,
      { method: "POST", body: JSON.stringify({ email: str(f, "email") || null }) },
    );
    const h = await headers();
    const host = h.get("x-forwarded-host") ?? h.get("host") ?? "localhost:3000";
    const proto = h.get("x-forwarded-proto") ?? (host.startsWith("localhost") ? "http" : "https");
    revalidatePath("/app/compradores");
    return {
      ok: "Convite gerado. Copie o link e envie ao comprador — ele aparece só agora.",
      link: `${proto}://${host}${out.invite_path}`,
      expires: out.invite_expires_at ?? undefined,
    };
  } catch (e) {
    if (e instanceof ApiError) return { error: e.message };
    throw e;
  }
}
