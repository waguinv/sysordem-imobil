import { ArrowRight, Banknote, CalendarClock, FileSignature, Receipt } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/primitives";
import { getMe } from "@/lib/session";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Tela inicial" };

const METRICS = [
  { label: "Recebido no mês", value: "R$ 0,00", note: "Disponível na Fase 3", icon: Banknote },
  { label: "A receber", value: "R$ 0,00", note: "Parcelas em aberto", icon: CalendarClock },
  { label: "Contas a pagar", value: "R$ 0,00", note: "Obrigações cadastradas", icon: Receipt },
  { label: "Vendas contratadas", value: "R$ 0,00", note: "Contratos ativos", icon: FileSignature },
];

type LotStatus = "disponivel" | "reservado" | "vendido" | "bloqueado";
const STATUS: Record<LotStatus, { label: string; cell: string; dot: string }> = {
  disponivel: { label: "Disponível", cell: "bg-lot-disponivel text-brand-deep", dot: "bg-lot-disponivel" },
  reservado: { label: "Reservado", cell: "bg-lot-reservado text-amber-950", dot: "bg-lot-reservado" },
  vendido: { label: "Vendido", cell: "bg-lot-vendido text-white", dot: "bg-lot-vendido" },
  bloqueado: { label: "Bloqueado", cell: "bg-lot-bloqueado text-white", dot: "bg-lot-bloqueado" },
};

/** Mapa ilustrativo (dados fictícios). A planta real chega na Fase 1. */
function demoStatus(i: number): LotStatus {
  if (i % 11 === 5) return "bloqueado";
  if (i % 7 === 2) return "reservado";
  if (i % 4 === 0) return "vendido";
  return "disponivel";
}

const MODULES = [
  { title: "Empreendimentos e lotes", phase: "Fase 1" },
  { title: "CRM, reservas e contratos", phase: "Fase 2" },
  { title: "Parcelas e financeiro", phase: "Fase 3" },
  { title: "Pix/boleto e conciliação", phase: "Fase 4" },
  { title: "Site público e portal", phase: "Fase 5" },
];

export default async function DashboardPage() {
  const me = await getMe();
  const firstName = me.full_name.split(" ")[0];
  return (
    <div className="space-y-6">
      <div>
        <p className="text-brand text-xs font-bold tracking-[0.12em] uppercase">Painel da empresa</p>
        <h1 className="text-brand-institutional mt-1 text-2xl font-bold sm:text-3xl">Olá, {firstName}</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Visão geral de vendas, recebimentos e lotes de {me.tenant.name}.
        </p>
      </div>

      <section aria-label="Indicadores financeiros" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {METRICS.map(({ label, value, note, icon: Icon }) => (
          <Card key={label} className="gap-2 py-4">
            <CardContent className="px-4">
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground text-xs sm:text-sm">{label}</span>
                <span className="bg-brand/10 text-brand-institutional grid size-8 place-items-center rounded-lg">
                  <Icon className="size-4" />
                </span>
              </div>
              <p className="text-brand-institutional mt-2 text-xl font-bold tracking-tight sm:text-2xl">
                {value}
              </p>
              <p className="text-muted-foreground text-[11px] sm:text-xs">{note}</p>
            </CardContent>
          </Card>
        ))}
      </section>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Mapa de lotes</CardTitle>
            <CardDescription>
              Exemplo visual — a planta real do empreendimento chega na Fase 1.
            </CardDescription>
          </div>
          <Badge variant="muted">Demonstração</Badge>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="text-muted-foreground flex flex-wrap gap-x-5 gap-y-2 text-xs">
            {Object.values(STATUS).map((s) => (
              <span key={s.label} className="flex items-center gap-2">
                <i className={cn("size-2.5 rounded-full", s.dot)} />
                {s.label}
              </span>
            ))}
          </div>
          <div
            className="bg-brand-surface grid grid-cols-4 gap-1.5 rounded-xl p-3 sm:grid-cols-8"
            aria-label="Mapa ilustrativo, não representa limites reais"
          >
            {Array.from({ length: 24 }, (_, i) => {
              const s = STATUS[demoStatus(i)];
              return (
                <div
                  key={i}
                  title={s.label}
                  className={cn("flex min-h-16 flex-col items-center justify-center rounded-md", s.cell)}
                >
                  <small className="text-[10px] opacity-80">Q{Math.floor(i / 8) + 1}</small>
                  <strong className="text-base">{String((i % 8) + 1).padStart(2, "0")}</strong>
                </div>
              );
            })}
          </div>
          <p className="text-muted-foreground text-xs">
            Valores e lotes exibidos são fictícios e não representam empreendimento real.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Próximos módulos</CardTitle>
            <CardDescription>
              Entregas incrementais, cada uma com testes e revisão de segurança.
            </CardDescription>
          </div>
        </CardHeader>
        <CardContent className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
          {MODULES.map((m) => (
            <div key={m.title} className="rounded-lg border p-3">
              <p className="text-sm font-medium">{m.title}</p>
              <p className="text-brand-muted mt-1 text-xs">{m.phase}</p>
            </div>
          ))}
        </CardContent>
      </Card>

      {me.permissions.includes("members:view") && (
        <Link
          href="/app/equipe"
          className="text-brand-institutional inline-flex items-center gap-1 text-sm font-medium hover:underline"
        >
          Ver equipe e acessos <ArrowRight className="size-4" />
        </Link>
      )}
    </div>
  );
}
