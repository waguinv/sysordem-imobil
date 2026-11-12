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
export function parseDecimalBR(input: string, maxDecimals = 2): string | null {
  const s = input.trim().replace(/\s|R\$|%/g, "");
  if (!s) return null;
  const normalized = s.includes(",") ? s.replace(/\./g, "").replace(",", ".") : s;
  return new RegExp(`^\\d+(\\.\\d{1,${maxDecimals}})?$`).test(normalized) ? normalized : null;
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

export const RESERVATION_STATUS: Record<
  string,
  { label: string; badge: "default" | "muted" | "warning" | "outline" }
> = {
  ativa: { label: "Ativa", badge: "warning" },
  convertida: { label: "Convertida em venda", badge: "default" },
  cancelada: { label: "Cancelada", badge: "muted" },
  expirada: { label: "Expirada", badge: "muted" },
};

/** "em 2 dias", "em 5 h", "vencida" — relativo a `now` (padrão: agora). */
export function timeLeft(iso: string, now: Date = new Date()): string {
  const ms = new Date(iso).getTime() - now.getTime();
  if (ms <= 0) return "vencida";
  const h = Math.floor(ms / 3_600_000);
  if (h >= 47) return `em ${Math.round(ms / 86_400_000)} dias`;
  if (h >= 1) return `em ${h} h`;
  return `em ${Math.max(1, Math.floor(ms / 60_000))} min`;
}

/** Mostra só o tipo e os 3 últimos dígitos do CPF/CNPJ (minimização de dados na tela). */
export function maskDoc(d: string | null): string {
  if (!d) return "—";
  return `${d.length === 11 ? "CPF" : "CNPJ"} •••${d.slice(-3)}`;
}

export const CONTRACT_STATUS: Record<
  string,
  { label: string; badge: "default" | "muted" | "warning" | "outline" }
> = {
  rascunho: { label: "Rascunho", badge: "warning" },
  assinado: { label: "Assinado", badge: "default" },
  cancelado: { label: "Cancelado", badge: "muted" },
};

export const ADJUSTMENT_INDEX: Record<string, string> = {
  nenhum: "Sem correção",
  IPCA: "IPCA",
  IGPM: "IGP-M",
  INCC: "INCC",
};

export function pct(value: string): string {
  return `${Number(value).toLocaleString("pt-BR", { maximumFractionDigits: 4 })}%`;
}

export function dateBR(iso: string | null): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${d}/${m}/${y}`;
}

export const AMORTIZATION: Record<string, { label: string; hint: string }> = {
  price: { label: "Price", hint: "parcelas iguais" },
  sac: { label: "SAC", hint: "parcelas decrescentes" },
};

type BadgeVariant = "default" | "muted" | "warning" | "outline";

export const LEAD_STATUS: Record<string, { label: string; badge: BadgeVariant }> = {
  novo: { label: "Novo", badge: "outline" },
  em_contato: { label: "Em contato", badge: "warning" },
  qualificado: { label: "Qualificado", badge: "warning" },
  convertido: { label: "Convertido", badge: "default" },
  perdido: { label: "Perdido", badge: "muted" },
};

export const LEAD_SOURCE: Record<string, string> = {
  site: "Site",
  indicacao: "Indicação",
  whatsapp: "WhatsApp",
  telefone: "Telefone",
  visita: "Visita ao estande",
  redes_sociais: "Redes sociais",
  outro: "Outro",
};

export const ACTIVITY_KIND: Record<string, string> = {
  nota: "Anotação",
  ligacao: "Ligação",
  whatsapp: "WhatsApp",
  email: "E-mail",
  visita: "Visita",
  status: "Sistema",
};

export const PROPOSAL_STATUS: Record<string, { label: string; badge: BadgeVariant }> = {
  aberta: { label: "Aberta", badge: "warning" },
  aceita: { label: "Aceita", badge: "default" },
  recusada: { label: "Recusada", badge: "muted" },
  cancelada: { label: "Cancelada", badge: "muted" },
  expirada: { label: "Expirada", badge: "muted" },
};

/** "5% de desconto", "2% acima da tabela" ou "preço de tabela". */
export function discountLabel(discountPct: string): string {
  const d = Number(discountPct);
  if (d === 0) return "preço de tabela";
  const v = Math.abs(d).toLocaleString("pt-BR", { maximumFractionDigits: 2 });
  return d > 0 ? `${v}% de desconto` : `${v}% acima da tabela`;
}

export const PAYMENT_METHOD: Record<string, string> = {
  pix: "Pix",
  boleto: "Boleto",
  transferencia: "Transferência",
  dinheiro: "Dinheiro",
  cartao: "Cartão",
  cheque: "Cheque",
  outro: "Outro",
};

/** Rótulo e cor da situação da parcela ("vencida" é aberta com vencimento passado). */
export function installmentStatus(i: { status: string; overdue: boolean; paid_principal: string }): {
  label: string;
  badge: BadgeVariant | "danger";
} {
  if (i.status === "paga") return { label: "Paga", badge: "default" };
  if (i.status === "cancelada") return { label: "Cancelada", badge: "muted" };
  const partial = Number(i.paid_principal) > 0;
  if (i.overdue) return { label: partial ? "Vencida (parcial)" : "Vencida", badge: "danger" };
  return { label: partial ? "Parcialmente paga" : "A vencer", badge: partial ? "warning" : "outline" };
}

/** "Entrada" para o nº 0; "3/120" para as demais. */
export function installmentLabel(n: number, total?: number): string {
  if (n === 0) return "Entrada";
  return total ? `${n}/${total}` : `Parcela ${n}`;
}
