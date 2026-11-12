import { FileText } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { Badge, Card, CardContent } from "@/components/ui/primitives";
import { brl, dateBR } from "@/lib/format";
import { portalApi, type PortalContract } from "@/lib/portal";

export const metadata: Metadata = { title: "Meus contratos" };

export default async function PortalHome({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const contracts = await portalApi<PortalContract[]>(slug, "/contracts");
  return (
    <div className="space-y-6">
      <h1 className="text-brand-institutional text-2xl font-bold">Meus contratos</h1>
      {contracts.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-2 py-10 text-center">
            <FileText className="text-brand-muted size-8" />
            <p className="text-muted-foreground text-sm">Nenhum contrato assinado ainda.</p>
          </CardContent>
        </Card>
      ) : (
        <ul className="space-y-4">
          {contracts.map((c) => (
            <li key={c.id}>
              <Link href={`/portal/${slug}/contratos/${c.id}`} className="block">
                <Card className="hover:border-brand/50 transition-colors">
                  <CardContent className="space-y-4">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div>
                        <p className="font-semibold">
                          {c.development_name} · Quadra {c.block_code} · Lote {c.lot_number}
                        </p>
                        <p className="text-muted-foreground text-xs">
                          Contrato {c.number} · {brl(c.total_price)}
                        </p>
                      </div>
                      {c.status === "distratado" ? (
                        <Badge variant="muted">Distratado</Badge>
                      ) : c.overdue_count > 0 ? (
                        <Badge variant="danger">{c.overdue_count} parcela(s) em atraso</Badge>
                      ) : (
                        <Badge>Em dia</Badge>
                      )}
                    </div>
                    <dl className="grid gap-3 text-sm sm:grid-cols-3">
                      <div>
                        <dt className="text-muted-foreground text-xs">Próximo vencimento</dt>
                        <dd className="font-medium tabular-nums">
                          {c.next_due ? `${dateBR(c.next_due.due_date)} · ${brl(c.next_due.remaining)}` : "—"}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground text-xs">Parcelas pagas</dt>
                        <dd className="font-medium">
                          {c.paid_count} de {c.paid_count + c.open_count}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground text-xs">Soma das parcelas em aberto</dt>
                        <dd className="font-medium tabular-nums">{brl(c.open_balance)}</dd>
                      </div>
                    </dl>
                  </CardContent>
                </Card>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
