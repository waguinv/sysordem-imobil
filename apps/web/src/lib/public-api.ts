import "server-only";
import { notFound } from "next/navigation";
import { API_URL } from "@/lib/session";

/** Tipos do site público — espelham os DTOs públicos da API (lista fechada de campos). */
export type PublicDevelopmentSummary = {
  id: string;
  name: string;
  kind: string;
  city: string | null;
  state: string | null;
  description: string;
  lots_total: number;
  lots_available: number;
  price_from: string | null;
};

export type PublicSite = {
  slug: string;
  name: string;
  tagline: string | null;
  about: string | null;
  contact: { phone: string | null; whatsapp: string | null; email: string | null };
  show_prices: boolean;
  lead_form_enabled: boolean;
  developments: PublicDevelopmentSummary[];
};

export type PublicLot = {
  id: string;
  number: string;
  area_m2: string;
  front_m: string | null;
  available: boolean;
  price: string | null;
};

export type PublicDevelopment = PublicDevelopmentSummary & {
  blocks: { code: string; lots: PublicLot[] }[];
};

/** Leitura pública (sem sessão). 404/422 da API → página "não encontrada". */
export async function publicApi<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}/api/v1/public/sites${path}`, { cache: "no-store" });
  if (res.status === 404 || res.status === 422) notFound();
  if (!res.ok) throw new Error(`Erro ${res.status}`);
  return (await res.json()) as T;
}
