"use server";

import { revalidatePath } from "next/cache";
import type { FormState } from "@/lib/actions-fase1";
import { api, ApiError } from "@/lib/session";
import type { BankAccount, StatementImport, StatementLineDetail } from "@/lib/types";

const str = (f: FormData, k: string) => String(f.get(k) ?? "").trim();
const MAX_BYTES = 2 * 1024 * 1024;

async function guard(fn: () => Promise<FormState>): Promise<FormState> {
  try {
    return await fn();
  } catch (e) {
    if (e instanceof ApiError) return { error: e.message };
    throw e;
  }
}

function refresh(lineId?: string) {
  revalidatePath("/app/financeiro/conciliacao");
  if (lineId) revalidatePath(`/app/financeiro/conciliacao/linhas/${lineId}`);
}

export async function createBankAccountAction(_p: FormState, f: FormData): Promise<FormState> {
  const name = str(f, "name");
  if (name.length < 2) return { error: "Informe o nome da conta." };
  return guard(async () => {
    const a = await api<BankAccount>("/bank-accounts", {
      method: "POST",
      body: JSON.stringify({
        name,
        bank_name: str(f, "bank_name") || null,
        branch: str(f, "branch") || null,
        account_number: str(f, "account_number") || null,
      }),
    });
    refresh();
    return { ok: `Conta ${a.name} cadastrada` };
  });
}

export async function setBankAccountActiveAction(
  accountId: string,
  active: boolean,
  _p: FormState,
  _f: FormData,
): Promise<FormState> {
  return guard(async () => {
    await api<BankAccount>(`/bank-accounts/${accountId}`, {
      method: "PATCH",
      body: JSON.stringify({ is_active: active }),
    });
    refresh();
    return { ok: active ? "Conta reativada" : "Conta desativada" };
  });
}

/** OFX de bancos brasileiros costuma vir em Windows-1252; o resto em UTF-8. */
function decode(buf: ArrayBuffer): string {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(buf);
  } catch {
    return new TextDecoder("windows-1252").decode(buf);
  }
}

export async function importStatementAction(
  accountId: string,
  _p: FormState,
  f: FormData,
): Promise<FormState> {
  const file = f.get("file");
  if (!(file instanceof File) || file.size === 0)
    return { error: "Escolha o arquivo do extrato (OFX ou CSV)." };
  if (file.size > MAX_BYTES) return { error: "Arquivo muito grande (máx. 2 MB) — divida por período." };
  const content = decode(await file.arrayBuffer());
  return guard(async () => {
    const r = await api<StatementImport>(`/bank-accounts/${accountId}/statements`, {
      method: "POST",
      body: JSON.stringify({ file_name: file.name.slice(0, 200), content }),
    });
    refresh();
    const dup = r.duplicates ? `, ${r.duplicates} já existiam` : "";
    return {
      ok: `${r.lines_new} lançamento(s) novo(s)${dup}; ${r.auto_matched} conciliado(s) automaticamente`,
    };
  });
}

export async function autoReconcileAction(
  accountId: string,
  _p: FormState,
  _f: FormData,
): Promise<FormState> {
  return guard(async () => {
    const r = await api<{ matched: number }>(`/bank-accounts/${accountId}/auto-reconcile`, {
      method: "POST",
    });
    refresh();
    return { ok: r.matched ? `${r.matched} lançamento(s) conciliado(s)` : "Nenhum par único encontrado" };
  });
}

export async function reconcileLineAction(lineId: string, _p: FormState, f: FormData): Promise<FormState> {
  const ids = f.getAll("target_ids").map(String).filter(Boolean);
  if (!ids.length) return { error: "Marque ao menos um lançamento do sistema." };
  return guard(async () => {
    await api<StatementLineDetail>(`/statement-lines/${lineId}/reconcile`, {
      method: "POST",
      body: JSON.stringify({ target_ids: ids }),
    });
    refresh(lineId);
    return { ok: "Lançamento conciliado" };
  });
}

async function withReason(
  lineId: string,
  path: "undo" | "ignore",
  f: FormData,
  ok: string,
): Promise<FormState> {
  const reason = str(f, "reason");
  if (reason.length < 3) return { error: "Informe o motivo (mín. 3 caracteres)." };
  return guard(async () => {
    await api<StatementLineDetail>(`/statement-lines/${lineId}/${path}`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
    refresh(lineId);
    return { ok };
  });
}

export async function undoLineAction(lineId: string, _p: FormState, f: FormData): Promise<FormState> {
  return withReason(lineId, "undo", f, "Conciliação desfeita");
}

export async function ignoreLineAction(lineId: string, _p: FormState, f: FormData): Promise<FormState> {
  return withReason(lineId, "ignore", f, "Lançamento ignorado");
}

export async function unignoreLineAction(lineId: string, _p: FormState, _f: FormData): Promise<FormState> {
  return guard(async () => {
    await api<StatementLineDetail>(`/statement-lines/${lineId}/unignore`, { method: "POST" });
    refresh(lineId);
    return { ok: "Lançamento voltou a pendente" };
  });
}
