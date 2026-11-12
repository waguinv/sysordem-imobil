import { LandPlot, MapPin, Plus } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { Badge, buttonVariants, Card, CardContent } from "@/components/ui/primitives";
import { brl, DEV_KIND, DEV_STATUS, LOT_STATUS } from "@/lib/format";
import { api, can, getMe } from "@/lib/session";
import type { Development } from "@/lib/types";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Empreendimentos" };

function StatusBar({ dev }: { dev: Development }) {
  if (!dev.lots_total) return <div className="bg-muted h-2 rounded-full" />;
  return (
    <div className="flex h-2 overflow-hidden rounded-full" aria-hidden>
      {(["vendido", "reservado", "disponivel", "bloqueado"] as const).map((k) =>
        dev.counts[k] ? (
          <span
            key={k}
            className={LOT_STATUS[k]!.dot}
            style={{ width: `${(dev.counts[k] / dev.lots_total) * 100}%` }}
          />
        ) : null,
      )}
    </div>
  );
}

export default async function EmpreendimentosPage() {
  const [me, devs] = await Promise.all([getMe(), api<Development[]>("/developments")]);
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-brand-institutional text-2xl font-bold">Empreendimentos</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Loteamentos, quadras e lotes de {me.tenant.name}.
          </p>
        </div>
        {can(me, "developments:manage") && (
          <Link href="/app/empreendimentos/novo" className={buttonVariants()}>
            <Plus /> Novo empreendimento
          </Link>
        )}
      </div>

      {devs.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-10 text-center">
            <span className="bg-brand/10 text-brand-institutional grid size-12 place-items-center rounded-full">
              <LandPlot className="size-6" />
            </span>
            <p className="font-medium">Nenhum empreendimento cadastrado</p>
            <p className="text-muted-foreground max-w-sm text-sm">
              Cadastre o primeiro loteamento e depois crie as quadras e os lotes.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {devs.map((d) => (
            <Link key={d.id} href={`/app/empreendimentos/${d.id}`} className="group">
              <Card className="group-hover:border-brand/60 h-full transition-colors">
                <CardContent className="space-y-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="text-brand-institutional truncate font-semibold">{d.name}</p>
                      <p className="text-muted-foreground flex items-center gap-1 text-xs">
                        <MapPin className="size-3" />
                        {[d.city, d.state].filter(Boolean).join(" / ") || "Local não informado"} ·{" "}
                        {DEV_KIND[d.kind] ?? d.kind}
                      </p>
                    </div>
                    <Badge variant={d.status === "em_vendas" ? "default" : "muted"}>
                      {DEV_STATUS[d.status] ?? d.status}
                    </Badge>
                  </div>
                  <StatusBar dev={d} />
                  <div className="grid grid-cols-3 gap-2 text-center text-xs">
                    <div>
                      <p className="text-brand-institutional text-lg font-bold">{d.lots_total}</p>
                      <p className="text-muted-foreground">lotes</p>
                    </div>
                    <div>
                      <p className="text-brand-institutional text-lg font-bold">{d.counts.disponivel}</p>
                      <p className="text-muted-foreground">disponíveis</p>
                    </div>
                    <div>
                      <p className={cn("text-brand-institutional text-lg font-bold")}>{d.counts.vendido}</p>
                      <p className="text-muted-foreground">vendidos</p>
                    </div>
                  </div>
                  <p className="text-muted-foreground border-t pt-2 text-xs">
                    Estoque disponível: <strong className="text-foreground">{brl(d.available_value)}</strong>
                  </p>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
