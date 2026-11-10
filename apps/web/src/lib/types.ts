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
};

export type Broker = {
  id: string;
  name: string;
  creci: string | null;
  email: string | null;
  phone: string | null;
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
