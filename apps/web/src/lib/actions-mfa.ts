"use server";

import { revalidatePath } from "next/cache";
import { api, ApiError } from "@/lib/session";

export type MfaState = {
  error?: string;
  ok?: string;
  /** passo 1 da ativação: chave para o aplicativo */
  secret?: string;
  otpauthUri?: string;
  /** códigos de recuperação — mostrados UMA vez */
  codes?: string[];
};

const str = (f: FormData, k: string) => String(f.get(k) ?? "").trim();

async function guard(fn: () => Promise<MfaState>): Promise<MfaState> {
  try {
    return await fn();
  } catch (e) {
    if (e instanceof ApiError) {
      return {
        error: e.status === 429 ? "Muitas tentativas. Aguarde alguns minutos." : e.message,
      };
    }
    throw e;
  }
}

function refresh() {
  revalidatePath("/app/conta/seguranca");
  revalidatePath("/app", "layout");
}

export async function startMfaAction(_p: MfaState, f: FormData): Promise<MfaState> {
  const password = String(f.get("password") ?? "");
  if (!password) return { error: "Informe sua senha atual." };
  return guard(async () => {
    const r = await api<{ secret: string; otpauth_uri: string }>("/auth/mfa/setup", {
      method: "POST",
      body: JSON.stringify({ password }),
    });
    return { secret: r.secret, otpauthUri: r.otpauth_uri };
  });
}

export async function confirmMfaAction(prev: MfaState, f: FormData): Promise<MfaState> {
  const code = str(f, "code");
  if (code.length < 6) return { ...prev, error: "Digite o código de 6 dígitos do aplicativo." };
  return guard(async () => {
    const r = await api<{ recovery_codes: string[] }>("/auth/mfa/enable", {
      method: "POST",
      body: JSON.stringify({ code }),
    });
    // sem revalidar: a tela recarregaria no estado "ligada" e os códigos sumiriam antes de serem vistos;
    // o botão "Já guardei os códigos" atualiza a tela
    return { ok: "Verificação em duas etapas ligada", codes: r.recovery_codes };
  }).then((s) => (s.error ? { ...prev, error: s.error } : s));
}

export async function disableMfaAction(_p: MfaState, f: FormData): Promise<MfaState> {
  const password = String(f.get("password") ?? "");
  const code = str(f, "code");
  if (!password || code.length < 6) return { error: "Informe a senha e um código." };
  return guard(async () => {
    await api("/auth/mfa/disable", { method: "POST", body: JSON.stringify({ password, code }) });
    refresh();
    return { ok: "Verificação em duas etapas desligada" };
  });
}

export async function newRecoveryCodesAction(_p: MfaState, f: FormData): Promise<MfaState> {
  const password = String(f.get("password") ?? "");
  const code = str(f, "code");
  if (!password || code.length < 6) return { error: "Informe a senha e um código." };
  return guard(async () => {
    const r = await api<{ recovery_codes: string[] }>("/auth/mfa/recovery-codes", {
      method: "POST",
      body: JSON.stringify({ password, code }),
    });
    return { ok: "Novos códigos gerados — os antigos deixaram de valer", codes: r.recovery_codes };
  });
}
