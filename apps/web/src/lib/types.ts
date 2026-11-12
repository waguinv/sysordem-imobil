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

export type LotDetail = Lot & { block_code: string; development_name: string; allowed_transitions: string[] };

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
