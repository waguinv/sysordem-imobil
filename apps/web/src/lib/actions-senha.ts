"use server";

import { clientIpHeaders } from "@/lib/client-ip";
import { revalidatePath } from "next/cache";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import type { FormState } from "@/lib/actions-fase1";
import { api, API_URL, ApiError, SESSION_COOKIE } from "@/lib/session";

const str = (f: FormData, k: string) => String(f.get(k) ?? "").trim();
const MIN = 12;

async function publicPost(path: string, body: unknown): Promise<Response | null> {
  try {
    return await fetch(`${API_URL}/api/v1${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(await clientIpHeaders()) },
      body: JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    return null;
  }
}

async function detail(res: Response, fallback: string): Promise<string> {
  const d = ((await res.json().catch(() => ({}))) as { detail?: unknown }).detail;
  return typeof d === "string" ? d : fallback;
}

function checkNew(f: FormData): string | null {
  const pw = String(f.get("new_password") ?? "");
  if (pw.length < MIN) return `A senha precisa de pelo menos ${MIN} caracteres.`;
  if (pw !== String(f.get("confirm") ?? "")) return "As senhas não conferem.";
  return null;
}

/** "Esqueci minha senha" (sem sessão). Resposta sempre igual: não revela quem tem conta. */
export async function forgotPasswordAction(_p: FormState, f: FormData): Promise<FormState> {
  const email = str(f, "email");
  if (!email.includes("@")) return { error: "Informe o e-mail da sua conta." };
  const res = await publicPost("/auth/password/forgot", {
    email,
    tenant_slug: str(f, "tenant").toLowerCase() || null,
  });
  if (!res) return { error: "Servidor indisponível. Tente novamente em instantes." };
  const body = (await res.json().catch(() => ({}))) as { email_enabled?: boolean };
  if (body.email_enabled === false) {
    return {
      error:
        "O envio de e-mail não está ligado neste sistema. Peça ao administrador da sua empresa para redefinir seu acesso.",
    };
  }
  return {
    ok: "Se houver uma conta com este e-mail, enviamos um link para criar uma senha nova. Confira também o spam.",
  };
}

/** Link do e-mail (redefinir ou convite): define a senha e leva ao login. */
export async function resetPasswordAction(token: string, _p: FormState, f: FormData): Promise<FormState> {
  const err = checkNew(f);
  if (err) return { error: err };
  const res = await publicPost("/auth/password/reset", {
    token,
    new_password: String(f.get("new_password")),
  });
  if (!res) return { error: "Servidor indisponível. Tente novamente em instantes." };
  if (!res.ok) return { error: await detail(res, "Não foi possível definir a senha.") };
  const { purpose, tenant_slug } = (await res.json()) as { purpose: string; tenant_slug: string | null };
  const q = new URLSearchParams({ senha: purpose === "convite" ? "criada" : "redefinida" });
  if (tenant_slug) q.set("empresa", tenant_slug);
  redirect(`/entrar?${q}`);
}

/** Trocar a senha logado: a API devolve uma sessão nova (as dos outros aparelhos caem). */
export async function changePasswordAction(_p: FormState, f: FormData): Promise<FormState> {
  const err = checkNew(f);
  if (err) return { error: err };
  try {
    const r = await api<{ access_token: string; expires_in: number }>("/auth/password/change", {
      method: "POST",
      body: JSON.stringify({
        current_password: String(f.get("current_password") ?? ""),
        new_password: String(f.get("new_password") ?? ""),
      }),
    });
    (await cookies()).set(SESSION_COOKIE, r.access_token, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax",
      path: "/",
      maxAge: r.expires_in,
    });
    return { ok: "Senha trocada. As sessões abertas em outros aparelhos foram encerradas." };
  } catch (e) {
    if (e instanceof ApiError) {
      return { error: e.status === 429 ? "Muitas tentativas. Aguarde alguns minutos." : e.message };
    }
    throw e;
  }
}

/** Equipe: adicionar pessoa (convite por e-mail ou senha inicial). */
export async function addMemberAction(_p: FormState, f: FormData): Promise<FormState> {
  const email = str(f, "email");
  const fullName = str(f, "full_name");
  const initial = String(f.get("initial_password") ?? "");
  if (!email.includes("@") || fullName.length < 2) return { error: "Informe nome e e-mail." };
  if (initial && initial.length < MIN)
    return { error: `A senha inicial precisa de pelo menos ${MIN} caracteres.` };
  try {
    const m = await api<{ invite_sent: boolean }>("/members", {
      method: "POST",
      body: JSON.stringify({
        email,
        full_name: fullName,
        role: str(f, "role") || "comercial",
        initial_password: initial || null,
        reason: "adicionado pela tela Equipe",
      }),
    });
    revalidatePath("/app/equipe");
    return {
      ok: m.invite_sent
        ? `Pronto: ${email} recebeu um e-mail para acessar.`
        : `Pronto: entregue a senha inicial a ${email} por um canal seguro.`,
    };
  } catch (e) {
    if (e instanceof ApiError) return { error: e.message };
    throw e;
  }
}
