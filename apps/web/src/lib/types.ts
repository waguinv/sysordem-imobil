export type StatusCounts = { disponivel: number; reservado: number; vendido: number; bloqueado: number };

export type Development = {
  id: string;
  name: string;
  kind: string;
  status: string;
  city: string | null;
  state: string | null;
  address: string | null;
  description: string | null;
  land_owner_id: string | null;
  lots_total: number;
  counts: StatusCounts;
  available_value: string;
};

export type Lot = {
  id: string;
  development_id: string;
  block_id: string;
  number: string;
  area_m2: string;
  front_m: string | null;
  price: string;
  status: string;
  version: number;
  notes: string | null;
};

export type LotDetail = Lot & {
  block_code: string;
  development_name: string;
  allowed_transitions: string[];
  active_reservation_id: string | null;
  contract_id: string | null;
};

export type DevelopmentMap = {
  development: Development;
  blocks: { id: string; code: string; lots: Lot[] }[];
};

export type LotHistory = {
  id: string;
  from_status: string | null;
  to_status: string;
  actor_name: string | null;
  reason: string | null;
  created_at: string;
};

export type LandOwner = {
  id: string;
  name: string;
  document: string | null;
  email: string | null;
  phone: string | null;
  notes: string | null;
};

export type Customer = {
  id: string;
  name: string;
  document: string | null;
  email: string | null;
  phone: string | null;
  city: string | null;
  state: string | null;
  notes: string | null;
  broker_id: string | null;
};

export type Broker = {
  id: string;
  name: string;
  creci: string | null;
  email: string | null;
  phone: string | null;
  is_active: boolean;
  user_id: string | null;
};

export type Member = {
  id: string;
  user_id: string;
  email: string;
  full_name: string;
  role: string;
  is_active: boolean;
};

export type Reservation = {
  id: string;
  status: "ativa" | "convertida" | "cancelada" | "expirada";
  price: string;
  expires_at: string;
  created_at: string;
  closed_at: string | null;
  close_reason: string | null;
  notes: string | null;
  lot_id: string;
  lot_number: string;
  block_code: string;
  development_id: string;
  development_name: string;
  customer_id: string;
  customer_name: string;
  broker_id: string | null;
  broker_name: string | null;
};

export type ContractTerms = {
  version: number;
  total_price: string;
  down_payment: string;
  installments_count: number;
  installment_amount: string;
  amortization: "price" | "sac";
  interest_monthly_pct: string;
  first_due_date: string | null;
  adjustment_index: string;
  late_fee_pct: string;
  late_interest_monthly_pct: string;
  notes: string | null;
  reason: string | null;
  created_at: string;
};

export type Contract = {
  id: string;
  number: string;
  status: "rascunho" | "assinado" | "cancelado";
  current_version: number;
  created_at: string;
  signed_at: string | null;
  cancelled_at: string | null;
  cancel_reason: string | null;
  lot_id: string;
  lot_number: string;
  block_code: string;
  development_id: string;
  development_name: string;
  customer_id: string;
  customer_name: string;
  broker_name: string | null;
  reservation_id: string | null;
  terms: ContractTerms;
};

export type ContractDetail = Contract & { versions: ContractTerms[] };

export type ContractPolicy = {
  default_interest_monthly_pct: string;
  max_interest_monthly_pct: string;
  default_amortization: "price" | "sac";
  allowed_amortizations: ("price" | "sac")[];
  default_adjustment_index: string;
  late_fee_pct: string;
  late_interest_monthly_pct: string;
  max_installments: number;
  min_down_payment_pct: string;
  allow_override_late_charges: boolean;
  is_default: boolean;
};

export type LeadStatus = "novo" | "em_contato" | "qualificado" | "convertido" | "perdido";

export type Lead = {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;
  source: string;
  status: LeadStatus;
  development_id: string | null;
  development_name: string | null;
  broker_id: string | null;
  broker_name: string | null;
  customer_id: string | null;
  lost_reason: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
};

export type LeadActivity = {
  id: string;
  kind: string;
  description: string;
  author_name: string | null;
  created_at: string;
};

export type LeadDetail = Lead & { activities: LeadActivity[] };

export type Proposal = {
  id: string;
  status: "aberta" | "aceita" | "recusada" | "cancelada" | "expirada";
  valid_until: string;
  total_price: string;
  list_price: string;
  discount_pct: string;
  down_payment: string;
  installments_count: number;
  installment_amount: string;
  amortization: "price" | "sac";
  interest_monthly_pct: string;
  first_due_date: string | null;
  notes: string | null;
  lot_id: string;
  lot_number: string;
  lot_status: string;
  block_code: string;
  development_id: string;
  development_name: string;
  customer_id: string;
  customer_name: string;
  broker_id: string | null;
  broker_name: string | null;
  lead_id: string | null;
  reservation_id: string | null;
  created_at: string;
  decided_at: string | null;
  decision_reason: string | null;
};
