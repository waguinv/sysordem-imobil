/* Formatação para exibição. Valores monetários chegam da API como string decimal exata;
   a conversão para número acontece só aqui, para exibir — nunca para calcular. */

const brlFmt = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const numFmt = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 });

export function brl(value: string | null | undefined): string {
  return value == null ? "—" : brlFmt.format(Number(value));
}

export function m2(value: string | null | undefined): string {
  return value == null ? "—" : `${numFmt.format(Number(value))} m²`;
}

export function dateTime(iso: string): string {
  return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

/** Aceita "150.000,50", "150000,50" ou "150000.50" e devolve "150000.50" (string, sem float). */
export function parseDecimalBR(input: string): string | null {
  const s = input.trim().replace(/\s|R\$/g, "");
  if (!s) return null;
  const normalized = s.includes(",") ? s.replace(/\./g, "").replace(",", ".") : s;
  return /^\d+(\.\d{1,2})?$/.test(normalized) ? normalized : null;
}

export const LOT_STATUS: Record<string, { label: string; cell: string; dot: string }> = {
  disponivel: { label: "Disponível", cell: "bg-lot-disponivel text-brand-deep", dot: "bg-lot-disponivel" },
  reservado: { label: "Reservado", cell: "bg-lot-reservado text-amber-950", dot: "bg-lot-reservado" },
  vendido: { label: "Vendido", cell: "bg-lot-vendido text-white", dot: "bg-lot-vendido" },
  bloqueado: { label: "Bloqueado", cell: "bg-lot-bloqueado text-white", dot: "bg-lot-bloqueado" },
};

export const DEV_STATUS: Record<string, string> = {
  planejamento: "Planejamento",
  lancamento: "Lançamento",
  em_vendas: "Em vendas",
  encerrado: "Encerrado",
};

export const DEV_KIND: Record<string, string> = {
  loteamento: "Loteamento",
  chacaras: "Chácaras",
  condominio: "Condomínio",
};
