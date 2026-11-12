import { ArrowRight, Banknote, CalendarClock, FileSignature, Receipt } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { OnboardingCard } from "@/components/billing/onboarding-card";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/primitives";
import { StatusLegend } from "@/components/lots/lot-map";
import { brl } from "@/lib/format";
import { api, getMe } from "@/lib/session";
import type { Onboarding } from "@/lib/billing";
import type { Development } from "@/lib/types";

export const metadata: Metadata = { title: "Tela inicial" };

const METRICS = [
  { label: "Recebido no mês", value: "R$ 0,00", note: "Disponível na Fase 3", icon: Banknote },
  { label: "A receber", value: "R$ 0,00", note: "Parcelas em aberto", icon: CalendarClock },
  { label: "Contas a pagar", value: "R$ 0,00", note: "Obrigações cadastradas", icon: Receipt },
  { label: "Vendas contratadas", value: "R$ 0,00", note: "Contratos ativos", icon: FileSignature },
];

const MODULES = [
  { title: "CRM, reservas e contratos", phase: "Fase 2" },
  { title: "Parcelas e financeiro", phase: "Fase 3" },
  { title: "Pix/boleto e conciliação", phase: "Fase 4" },
  { title: "Site público e portal", phase: "Fase 5" },
];

export default async function DashboardPage() {
  const [me, devs, onboarding] = await Promise.all([
    getMe(),
    api<Development[]>("/developments"),
    api<Onboarding>("/onboarding"),
  ]);
  const totals = { disponivel: 0, reservado: 0, vendido: 0, bloqueado: 0 };
  for (const d of devs)
    for (const k of Object.keys(totals) as (keyof typeof totals)[]) totals[k] += d.counts[k];
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
      <OnboardingCard o={onboarding} />

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
            <CardTitle>Lotes por situação</CardTitle>
            <CardDescription>Todos os empreendimentos da empresa.</CardDescription>
          </div>
          <Link
            href="/app/empreendimentos"
            className="text-brand-institutional inline-flex shrink-0 items-center gap-1 text-sm font-medium hover:underline"
          >
            Ver mapas <ArrowRight className="size-4" />
          </Link>
        </CardHeader>
        <CardContent className="space-y-4">
          <StatusLegend counts={totals} />
          {devs.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              Nenhum empreendimento ainda.{" "}
              {me.permissions.includes("developments:manage") && (
                <Link
                  href="/app/empreendimentos/novo"
                  className="text-brand-institutional font-medium underline"
                >
                  Cadastrar o primeiro
                </Link>
              )}
            </p>
          ) : (
            <ul className="divide-y">
              {devs.map((d) => (
                <li key={d.id}>
                  <Link
                    href={`/app/empreendimentos/${d.id}`}
                    className="hover:bg-brand-surface flex flex-wrap items-center justify-between gap-2 rounded-md px-2 py-2.5"
                  >
                    <span className="text-sm font-medium">{d.name}</span>
                    <span className="text-muted-foreground text-xs">
                      {d.lots_total} lotes · {d.counts.disponivel} disponíveis · {brl(d.available_value)} em
                      estoque
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
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
        <CardContent className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
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
