import { CalendarClock } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Badge, Card, CardContent } from "@/components/ui/primitives";
import { brl, dateTime, RESERVATION_STATUS, timeLeft } from "@/lib/format";
import { api, can, getMe } from "@/lib/session";
import type { Reservation } from "@/lib/types";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Reservas" };

const FILTERS = [
  { key: "ativa", label: "Ativas" },
  { key: "", label: "Todas" },
  { key: "convertida", label: "Convertidas" },
  { key: "expirada", label: "Expiradas" },
  { key: "cancelada", label: "Canceladas" },
];

export default async function ReservasPage({ searchParams }: { searchParams: Promise<{ status?: string }> }) {
  const me = await getMe();
  if (!can(me, "reservations:view")) redirect("/app");
  const { status = "ativa" } = await searchParams;
  const valid = FILTERS.some((f) => f.key === status) ? status : "ativa";
  const list = await api<Reservation[]>(`/reservations${valid ? `?status=${valid}` : ""}`);
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-brand-institutional text-2xl font-bold">Reservas</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Para reservar, abra o lote no mapa do empreendimento. Reservas vencidas liberam o lote
          automaticamente.
        </p>
      </div>
      <nav className="flex flex-wrap gap-2" aria-label="Filtrar reservas">
        {FILTERS.map((f) => (
          <Link
            key={f.key || "todas"}
            href={`/app/reservas${f.key ? `?status=${f.key}` : "?status="}`}
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
              <CalendarClock className="text-brand-muted size-8" />
              <p className="text-muted-foreground text-sm">Nenhuma reserva nesta lista.</p>
            </div>
          ) : (
            <ul className="divide-y">
              {list.map((r) => {
                const st = RESERVATION_STATUS[r.status]!;
                return (
                  <li key={r.id}>
                    <Link
                      href={`/app/lotes/${r.lot_id}`}
                      className="hover:bg-brand-surface -mx-2 flex flex-wrap items-center justify-between gap-2 rounded-md px-2 py-3"
                    >
                      <div className="min-w-0">
                        <p className="text-sm font-medium">
                          {r.development_name} · Quadra {r.block_code} · Lote {r.lot_number}
                        </p>
                        <p className="text-muted-foreground text-xs">
                          {r.customer_name}
                          {r.broker_name && ` · corretor ${r.broker_name}`} · {brl(r.price)}
                        </p>
                      </div>
                      <div className="flex items-center gap-2 text-xs">
                        {r.status === "ativa" ? (
                          <span className="text-muted-foreground" title={dateTime(r.expires_at)}>
                            vence {timeLeft(r.expires_at)}
                          </span>
                        ) : (
                          r.closed_at && (
                            <span className="text-muted-foreground">{dateTime(r.closed_at)}</span>
                          )
                        )}
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
