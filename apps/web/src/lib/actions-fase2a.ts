"use server";

import { revalidatePath } from "next/cache";
import type { FormState } from "@/lib/actions-fase1";
import { api, ApiError } from "@/lib/session";

const str = (f: FormData, k: string) => String(f.get(k) ?? "").trim();
const opt = (f: FormData, k: string) => str(f, k) || null;

async function run(fn: () => Promise<unknown>, okMsg: string, paths: string[]): Promise<FormState> {
  try {
    await fn();
  } catch (e) {
    if (e instanceof ApiError) return { error: e.message };
    throw e;
  }
  for (const p of paths) revalidatePath(p);
  return { ok: okMsg };
}

export async function createCustomerAction(_p: FormState, f: FormData): Promise<FormState> {
  return run(
    () =>
      api("/customers", {
        method: "POST",
        body: JSON.stringify({
          name: str(f, "name"),
          document: opt(f, "document"),
          email: opt(f, "email"),
          phone: opt(f, "phone"),
          city: opt(f, "city"),
          state: opt(f, "state"),
        }),
      }),
    "Comprador cadastrado",
    ["/app/compradores"],
  );
}

export async function createBrokerAction(_p: FormState, f: FormData): Promise<FormState> {
  return run(
    () =>
      api("/brokers", {
        method: "POST",
        body: JSON.stringify({
          name: str(f, "name"),
          creci: opt(f, "creci"),
          document: opt(f, "document"),
          email: opt(f, "email"),
          phone: opt(f, "phone"),
          commission_pct: opt(f, "commission_pct")?.replace(",", ".") ?? null,
        }),
      }),
    "Corretor cadastrado",
    ["/app/corretores"],
  );
}

export async function toggleBrokerAction(
  brokerId: string,
  active: boolean,
  _p: FormState,
): Promise<FormState> {
  return run(
    () =>
      api(`/brokers/${brokerId}`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: active, reason: active ? "reativado" : "desativado" }),
      }),
    active ? "Corretor reativado" : "Corretor desativado",
    ["/app/corretores"],
  );
}

export async function createReservationAction(lotId: string, _p: FormState, f: FormData): Promise<FormState> {
  const hours = Number(str(f, "hours") || "0");
  return run(
    () =>
      api("/reservations", {
        method: "POST",
        body: JSON.stringify({
          lot_id: lotId,
          customer_id: str(f, "customer_id"),
          broker_id: opt(f, "broker_id"),
          hours: hours > 0 ? hours : null,
          notes: opt(f, "notes"),
        }),
      }),
    "Lote reservado",
    [`/app/lotes/${lotId}`, "/app/reservas"],
  );
}

export async function closeReservationAction(
  resId: string,
  lotId: string,
  kind: "cancel",
  _p: FormState,
  f: FormData,
): Promise<FormState> {
  const reason = str(f, "reason");
  if (reason.length < 3) return { error: "Informe o motivo (mín. 3 caracteres)." };
  return run(
    () => api(`/reservations/${resId}/${kind}`, { method: "POST", body: JSON.stringify({ reason }) }),
    "Reserva cancelada",
    [`/app/lotes/${lotId}`, "/app/reservas"],
  );
}
