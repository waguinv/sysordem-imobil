import { HandCoins } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Badge, Card, CardContent } from "@/components/ui/primitives";
import { brl, dateBR, discountLabel, PROPOSAL_STATUS } from "@/lib/format";
import { api, can, getMe } from "@/lib/session";
import type { Proposal } from "@/lib/types";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Propostas" };

const FILTERS = [
  { key: "aberta", label: "Abertas" },
  { key: "", label: "Todas" },
  { key: "aceita", label: "Aceitas" },
  { key: "recusada", label: "Recusadas" },
  { key: "expirada", label: "Expiradas" },
  { key: "cancelada", label: "Canceladas" },
];

export default async function PropostasPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  const me = await getMe();
  if (!can(me, "proposals:view")) redirect("/app");
  const { status = "aberta" } = await searchParams;
  const valid = FILTERS.some((f) => f.key === status) ? status : "aberta";
  const list = await api<Proposal[]>(`/proposals${valid ? `?status=${valid}` : ""}`);
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-brand-institutional text-2xl font-bold">Propostas</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Para propor, abra o lote no mapa do empreendimento. Aceitar uma proposta reserva o lote para o
          cliente; as condições aceitas preenchem o contrato.
        </p>
      </div>
      <nav className="flex flex-wrap gap-2" aria-label="Filtrar propostas">
        {FILTERS.map((f) => (
          <Link
            key={f.key || "todas"}
            href={`/app/propostas?status=${f.key}`}
            aria-current={valid === f.key ? "page" : undefined}
            className={cn(
              "rounded-full border px-3 py-1 text-sm",
              valid === f.key
                ? "bg-brand-institutional border-brand-institutional text-white"
                : "hover:bg-accent bg-white",
            )}
          >
            {f.label}
          </Link>
        ))}
      </nav>
      <Card>
        <CardContent>
          {list.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-8 text-center">
              <HandCoins className="text-brand-muted size-8" />
              <p className="text-muted-foreground text-sm">Nenhuma proposta nesta lista.</p>
            </div>
          ) : (
            <ul className="divide-y">
              {list.map((p) => {
                const st = PROPOSAL_STATUS[p.status]!;
                return (
                  <li key={p.id}>
                    <Link
                      href={`/app/propostas/${p.id}`}
                      className="hover:bg-accent/60 -mx-2 flex flex-wrap items-center justify-between gap-3 rounded-lg px-2 py-3"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">
                          {p.customer_name} · {p.development_name} · Q{p.block_code} L{p.lot_number}
                        </p>
                        <p className="text-muted-foreground truncate text-xs">
                          {brl(p.total_price)} ({discountLabel(p.discount_pct)}) ·{" "}
                          {p.installments_count === 0 ? "à vista" : `entrada + ${p.installments_count}×`} ·{" "}
                          {p.broker_name ?? "sem corretor"} · válida até {dateBR(p.valid_until)}
                        </p>
                      </div>
                      <Badge variant={st.badge}>{st.label}</Badge>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
