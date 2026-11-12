import "server-only";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { API_URL } from "@/lib/session";

/** Cookie httpOnly do portal do comprador — separado da sessão da equipe (token com `aud` próprio). */
export const PORTAL_COOKIE = "imobil_portal";

export class PortalError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

/** Chamada à API do portal com o token do comprador. Sem sessão (ou expirada) → login do portal. */
export async function portalApi<T>(slug: string, path: string, init: RequestInit = {}): Promise<T> {
  const token = (await cookies()).get(PORTAL_COOKIE)?.value;
  const loginUrl = `/portal/${encodeURIComponent(slug)}/entrar`;
  if (!token) redirect(loginUrl);
  const res = await fetch(`${API_URL}/api/v1/portal${path}`, {
    ...init,
    cache: "no-store",
    headers: { ...init.headers, Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
  });
  if (res.status === 401) redirect(`${loginUrl}?expirada=1`);
  if (!res.ok) {
    let msg = `Erro ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") msg = body.detail;
    } catch {
      /* corpo não-JSON */
    }
    throw new PortalError(res.status, msg);
  }
  return (await res.json()) as T;
}

export type PortalMe = { customer_name: string; email: string; company_name: string; company_slug: string };

export type PortalCharge = {
  id: string;
  method: "pix" | "boleto";
  amount: string;
  due_date: string;
  pix_code: string | null;
  boleto_line: string | null;
};

export type PortalContract = {
  id: string;
  number: string;
  status: "assinado" | "distratado";
  signed_at: string | null;
  development_name: string;
  block_code: string;
  lot_number: string;
  area_m2: string;
  total_price: string;
  paid_count: number;
  open_count: number;
  overdue_count: number;
  open_balance: string;
  next_due: { number: number; due_date: string; remaining: string } | null;
};

export type PortalInstallment = {
  id: string;
  number: number;
  label: string;
  due_date: string;
  amount: string;
  remaining: string;
  status: "aberta" | "paga" | "renegociada" | "cancelada";
  overdue: boolean;
  days_late: number;
  paid_at: string | null;
  pending_charge: PortalCharge | null;
};

export type PortalContractDetail = PortalContract & {
  renegotiated: boolean;
  terms: {
    total_price: string;
    down_payment: string;
    installments_count: number;
    amortization: string;
    interest_monthly_pct: string;
    adjustment_index: string;
    late_fee_pct: string;
    late_interest_monthly_pct: string;
  };
  installments: PortalInstallment[];
  payments: {
    received_on: string;
    installment_number: number;
    installment_label: string;
    amount: string;
    principal: string;
    late_charges: string;
    method: string;
    reversed: boolean;
  }[];
};

export type PortalStatement = {
  year: number;
  company_name: string;
  customer_name: string;
  customer_document: string | null;
  lines: {
    received_on: string;
    contract_number: string;
    installment_number: number;
    amount: string;
    principal: string;
    late_charges: string;
  }[];
  total: string;
  total_principal: string;
  total_late_charges: string;
};
