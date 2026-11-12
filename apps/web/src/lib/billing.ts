/* Tipos e rótulos da assinatura SaaS (Fase 5c) — espelham a API. */

export type PlanInfo = {
  code: string;
  name: string;
  monthly_price: string;
  max_developments: number | null;
  max_lots: number | null;
  max_users: number | null;
};

export type SubscriptionState = "sem_assinatura" | "teste" | "ativa" | "em_atraso" | "suspensa" | "cancelada";

export type BillingStatus = {
  state: SubscriptionState;
  plan_code: string | null;
  plan_name: string | null;
  trial_ends_at: string | null;
  covered_until: string | null;
  grace_until: string | null;
  cancelled: boolean;
  can_write: boolean;
};

export type SubscriptionInvoice = {
  id: string;
  plan_code: string;
  period_start: string;
  period_end: string;
  due_date: string;
  amount: string;
  status: "aberta" | "paga" | "cancelada";
  method: string | null;
  pix_code: string | null;
  boleto_line: string | null;
  paid_at: string | null;
};

export type Billing = {
  status: BillingStatus;
  usage: { developments: number; lots: number; users: number };
  plans: PlanInfo[];
  invoices: SubscriptionInvoice[];
  amount_due: string;
  sandbox: boolean;
};

export type Onboarding = {
  steps: { key: string; title: string; done: boolean; href: string }[];
  done: number;
  total: number;
};

export const SUBSCRIPTION_STATE: Record<
  SubscriptionState,
  { label: string; badge: "default" | "muted" | "warning" | "outline" | "danger" }
> = {
  sem_assinatura: { label: "Sem assinatura", badge: "muted" },
  teste: { label: "Teste grátis", badge: "outline" },
  ativa: { label: "Ativa", badge: "default" },
  em_atraso: { label: "Pagamento em atraso", badge: "warning" },
  suspensa: { label: "Suspensa — só consulta", badge: "danger" },
  cancelada: { label: "Cancelada — só consulta", badge: "danger" },
};

export function limitLabel(v: number | null, unit: string): string {
  return v === null ? `${unit} ilimitados` : `até ${v.toLocaleString("pt-BR")} ${unit}`;
}

/** Dias inteiros de hoje até a data (negativo = passou). */
export function daysUntil(iso: string, now: Date = new Date()): number {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  const target = Date.UTC(y!, m! - 1, d!);
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((target - today) / 86_400_000);
}
