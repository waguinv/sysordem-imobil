import { FileSignature } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Badge, Card, CardContent } from "@/components/ui/primitives";
import { brl, CONTRACT_STATUS, dateTime } from "@/lib/format";
import { api, can, getMe } from "@/lib/session";
import type { Contract } from "@/lib/types";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Contratos" };

const FILTERS = [
  { key: "", label: "Todos" },
  { key: "rascunho", label: "Rascunhos" },
  { key: "assinado", label: "Assinados" },
  { key: "cancelado", label: "Cancelados" },
];

export default async function ContratosPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  const me = await getMe();
  if (!can(me, "contracts:view")) redirect("/app");
  const { status = "" } = await searchParams;
  const valid = FILTERS.some((f) => f.key === status) ? status : "";
  const list = await api<Contract[]>(`/contracts${valid ? `?status=${valid}` : ""}`);
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-brand-institutional text-2xl font-bold">Contratos</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Contratos nascem de uma reserva ativa (na tela do lote). Assinado, o lote fica vendido.
        </p>
      </div>
      <nav className="flex flex-wrap gap-2" aria-label="Filtrar contratos">
        {FILTERS.map((f) => (
          <Link
            key={f.key || "todos"}
            href={`/app/contratos${f.key ? `?status=${f.key}` : ""}`}
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
              <FileSignature className="text-brand-muted size-8" />
              <p className="text-muted-foreground text-sm">Nenhum contrato nesta lista.</p>
            </div>
          ) : (
            <ul className="divide-y">
              {list.map((c) => {
                const st = CONTRACT_STATUS[c.status]!;
                return (
                  <li key={c.id}>
                    <Link
                      href={`/app/contratos/${c.id}`}
                      className="hover:bg-brand-surface -mx-2 flex flex-wrap items-center justify-between gap-2 rounded-md px-2 py-3"
                    >
                      <div className="min-w-0">
                        <p className="text-sm font-medium">
                          Nº {c.number} · {c.customer_name}
                        </p>
                        <p className="text-muted-foreground text-xs">
                          {c.development_name} · Quadra {c.block_code} · Lote {c.lot_number} ·{" "}
                          {brl(c.terms.total_price)}
                        </p>
                      </div>
                      <div className="flex items-center gap-2 text-xs">
                        <span className="text-muted-foreground">{dateTime(c.signed_at ?? c.created_at)}</span>
                        <Badge variant={st.badge}>{st.label}</Badge>
                      </div>
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
