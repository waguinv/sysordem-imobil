"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { API_URL, SESSION_COOKIE } from "@/lib/session";

export type LoginState = { error?: string; values?: { tenant: string; email: string } };

export async function loginAction(_prev: LoginState, form: FormData): Promise<LoginState> {
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
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tenant_slug: tenant, email, password }),
      cache: "no-store",
    });
  } catch {
    return { error: "Servidor indisponível. Tente novamente em instantes.", values };
  }
  if (res.status === 429) return { error: "Muitas tentativas. Aguarde alguns minutos.", values };
  if (!res.ok) return { error: "Empresa, e-mail ou senha incorretos.", values };

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

export async function logoutAction(): Promise<void> {
  (await cookies()).delete(SESSION_COOKIE);
  redirect("/entrar");
}
