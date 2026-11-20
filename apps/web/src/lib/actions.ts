"use server";

import { clientIpHeaders } from "@/lib/client-ip";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { API_URL, SESSION_COOKIE } from "@/lib/session";

export type LoginState = {
  error?: string;
  values?: { tenant: string; email: string };
  /** 2ª etapa: senha conferida, falta o código do aplicativo (prova curta, não é token de acesso) */
  mfaToken?: string;
};

async function startSession(res: Response): Promise<never> {
  const { access_token, expires_in } = (await res.json()) as { access_token: string; expires_in: number };
  (await cookies()).set(SESSION_COOKIE, access_token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: expires_in,
  });
  redirect("/app");
}

async function mfaStep(prev: LoginState, form: FormData): Promise<LoginState> {
  const mfaToken = String(form.get("mfa_token") ?? "");
  const code = String(form.get("code") ?? "").trim();
  const keep = { values: prev.values, mfaToken };
  if (code.length < 6)
    return { ...keep, error: "Informe o código de 6 dígitos (ou um código de recuperação)." };
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/v1/auth/mfa/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(await clientIpHeaders()) },
      body: JSON.stringify({ mfa_token: mfaToken, code }),
      cache: "no-store",
    });
  } catch {
    return { ...keep, error: "Servidor indisponível. Tente novamente em instantes." };
  }
  if (res.status === 429) return { ...keep, error: "Muitas tentativas. Aguarde alguns minutos." };
  if (!res.ok) {
    const detail = ((await res.json().catch(() => ({}))) as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.includes("expirou"))
      return { values: prev.values, error: detail };
    return { ...keep, error: "Código inválido. Confira a hora do celular e use o código atual." };
  }
  return startSession(res);
}

export async function loginAction(prev: LoginState, form: FormData): Promise<LoginState> {
  if (form.get("mfa_token")) return mfaStep(prev, form);
  const tenant = String(form.get("tenant") ?? "")
    .trim()
    .toLowerCase();
  const email = String(form.get("email") ?? "").trim();
  const password = String(form.get("password") ?? "");
  const values = { tenant, email };
  if (!tenant || !email || !password) return { error: "Preencha empresa, e-mail e senha.", values };

  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(await clientIpHeaders()) },
      body: JSON.stringify({ tenant_slug: tenant, email, password }),
      cache: "no-store",
    });
  } catch {
    return { error: "Servidor indisponível. Tente novamente em instantes.", values };
  }
  if (res.status === 429) return { error: "Muitas tentativas. Aguarde alguns minutos.", values };
  if (!res.ok) return { error: "Empresa, e-mail ou senha incorretos.", values };

  const body = (await res.clone().json()) as { mfa_required?: boolean; mfa_token?: string };
  if (body.mfa_required && body.mfa_token) return { values, mfaToken: body.mfa_token };
  return startSession(res);
}

export async function logoutAction(): Promise<void> {
  (await cookies()).delete(SESSION_COOKIE);
  redirect("/entrar");
}
