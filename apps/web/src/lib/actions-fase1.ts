"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { parseDecimalBR } from "@/lib/format";
import { api, ApiError } from "@/lib/session";
import type { Development } from "@/lib/types";

export type FormState = { error?: string; ok?: string };

const str = (f: FormData, k: string) => String(f.get(k) ?? "").trim();
const opt = (f: FormData, k: string) => str(f, k) || null;

async function run(fn: () => Promise<unknown>, okMsg: string, path?: string): Promise<FormState> {
  try {
    await fn();
  } catch (e) {
    if (e instanceof ApiError) return { error: e.message };
    throw e; // inclui o redirect do Next
  }
  if (path) revalidatePath(path);
  return { ok: okMsg };
}

function decimal(f: FormData, k: string, label: string): string {
  const v = parseDecimalBR(str(f, k));
  if (v === null) throw new ApiError(422, `${label} inválido (use 150000,00)`);
  return v;
}

async function guarded(fn: () => Promise<FormState>): Promise<FormState> {
  try {
    return await fn();
  } catch (e) {
    if (e instanceof ApiError) return { error: e.message };
    throw e;
  }
}

// ── Empreendimentos ──

export async function createDevelopmentAction(_p: FormState, f: FormData): Promise<FormState> {
  let dev: Development | undefined;
  const res = await run(async () => {
    dev = await api<Development>("/developments", {
      method: "POST",
      body: JSON.stringify({
        name: str(f, "name"),
        kind: str(f, "kind") || "loteamento",
        status: str(f, "status") || "planejamento",
        city: opt(f, "city"),
        state: opt(f, "state"),
        address: opt(f, "address"),
        description: opt(f, "description"),
        land_owner_id: opt(f, "land_owner_id"),
      }),
    });
  }, "Empreendimento criado");
  if (res.error || !dev) return res;
  redirect(`/app/empreendimentos/${dev.id}`);
}

export async function updateDevelopmentStatusAction(
  devId: string,
  _p: FormState,
  f: FormData,
): Promise<FormState> {
  return run(
    () =>
      api(`/developments/${devId}`, {
        method: "PATCH",
        body: JSON.stringify({
          status: str(f, "status"),
          reason: str(f, "reason") || "atualização de status",
        }),
      }),
    "Status atualizado",
    `/app/empreendimentos/${devId}`,
  );
}

export async function createBlockAction(devId: string, _p: FormState, f: FormData): Promise<FormState> {
  return run(
    () =>
      api(`/developments/${devId}/blocks`, {
        method: "POST",
        body: JSON.stringify({ code: str(f, "code") }),
      }),
    "Quadra criada",
    `/app/empreendimentos/${devId}`,
  );
}

export async function createLotsBatchAction(devId: string, _p: FormState, f: FormData): Promise<FormState> {
  return guarded(() =>
    run(
      () =>
        api(`/developments/${devId}/lots/batch`, {
          method: "POST",
          body: JSON.stringify({
            block_id: str(f, "block_id"),
            first: Number(str(f, "first")),
            last: Number(str(f, "last")),
            area_m2: decimal(f, "area_m2", "Área"),
            front_m: str(f, "front_m") ? decimal(f, "front_m", "Testada") : null,
            price: decimal(f, "price", "Preço"),
          }),
        }),
      "Lotes criados",
      `/app/empreendimentos/${devId}`,
    ),
  );
}

// ── Lotes ──

export async function changeLotStatusAction(
  lotId: string,
  version: number,
  _p: FormState,
  f: FormData,
): Promise<FormState> {
  const reason = str(f, "reason");
  if (reason.length < 3) return { error: "Informe o motivo (mín. 3 caracteres)." };
  return run(
    () =>
      api(`/lots/${lotId}/status`, {
        method: "POST",
        body: JSON.stringify({ to_status: str(f, "to_status"), expected_version: version, reason }),
      }),
    "Status alterado",
    `/app/lotes/${lotId}`,
  );
}

export async function updateLotAction(
  lotId: string,
  version: number,
  _p: FormState,
  f: FormData,
): Promise<FormState> {
  const reason = str(f, "reason");
  if (reason.length < 3) return { error: "Informe o motivo (mín. 3 caracteres)." };
  return guarded(() =>
    run(
      () =>
        api(`/lots/${lotId}`, {
          method: "PATCH",
          body: JSON.stringify({
            price: decimal(f, "price", "Preço"),
            area_m2: decimal(f, "area_m2", "Área"),
            front_m: str(f, "front_m") ? decimal(f, "front_m", "Testada") : null,
            notes: opt(f, "notes"),
            expected_version: version,
            reason,
          }),
        }),
      "Lote atualizado",
      `/app/lotes/${lotId}`,
    ),
  );
}

// ── Proprietários ──

export async function createLandOwnerAction(_p: FormState, f: FormData): Promise<FormState> {
  return run(
    () =>
      api("/land-owners", {
        method: "POST",
        body: JSON.stringify({
          name: str(f, "name"),
          document: opt(f, "document"),
          email: opt(f, "email"),
          phone: opt(f, "phone"),
          notes: opt(f, "notes"),
        }),
      }),
    "Proprietário cadastrado",
    "/app/proprietarios",
  );
}
