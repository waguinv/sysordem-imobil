import { HandCoins } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { ActionForm } from "@/components/ui/action-form";
import {
  Badge,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Input,
} from "@/components/ui/primitives";
import { settleAction } from "@/lib/actions-fase3b";
import { brl, COMMISSION_BASIS, dateTime, pct } from "@/lib/format";
import { api, can, getMe } from "@/lib/session";
import type { Balance, FinanceSettings, ParticipationEntry, ParticipationKind } from "@/lib/types";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Comissões e repasses" };

const TABS: { key: ParticipationKind; label: string; who: string }[] = [
  { key: "comissao", label: "Comissões de corretores", who: "Corretor" },
  { key: "proprietario", label: "Repasses ao proprietário da área", who: "Proprietário" },
];

function inDays(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() + n);
  return d.toLocaleDateString("sv-SE", { timeZone: "America/Sao_Paulo" });
}

export default async function ComissoesPage({ searchParams }: { searchParams: Promise<{ tipo?: string }> }) {
  const me = await getMe();
  if (!can(me, "finance:view")) redirect("/app");
  const { tipo } = await searchParams;
  const tab = TABS.find((t) => t.key === tipo) ?? TABS[0]!;
  const [balances, entries, settings] = await Promise.all([
    api<Balance[]>(`/participations/balances?kind=${tab.key}`),
    api<ParticipationEntry[]>(`/participations?kind=${tab.key}&limit=50`),
    api<FinanceSettings>("/settings/finance"),
  ]);
  const manage = can(me, "finance:manage");
  const basis = COMMISSION_BASIS[settings.commission_basis]!;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-brand-institutional text-2xl font-bold">Comissões e repasses</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Os valores são lançados automaticamente.{" "}
          {tab.key === "comissao"
            ? `Comissão: ${basis.label.toLowerCase()} — ${basis.hint} (padrão ${pct(settings.default_commission_pct)}).`
            : "Repasse: % do empreendimento sobre cada parcela recebida."}{" "}
          Estornos de recebimento geram lançamentos negativos. Para pagar, gere a conta a pagar do
          beneficiário.
          {can(me, "settings:manage") && (
            <>
              {" "}
              <Link href="/app/configuracoes/financeiro" className="text-brand-institutional underline">
                Ajustar regras
              </Link>
              .
            </>
          )}
        </p>
      </div>

      <nav className="flex flex-wrap gap-2" aria-label="Tipo">
        {TABS.map((t) => (
          <Link
            key={t.key}
            href={`/app/comissoes?tipo=${t.key}`}
            aria-current={tab.key === t.key ? "page" : undefined}
            className={cn(
              "rounded-full border px-3 py-1 text-sm",
              tab.key === t.key
                ? "bg-brand-institutional border-brand-institutional text-white"
                : "hover:bg-accent bg-white",
            )}
          >
            {t.label}
          </Link>
        ))}
      </nav>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>A acertar por {tab.who.toLowerCase()}</CardTitle>
          </div>
        </CardHeader>
        <CardContent>
          {balances.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-8 text-center">
              <HandCoins className="text-brand-muted size-8" />
              <p className="text-muted-foreground text-sm">Nenhum lançamento ainda.</p>
            </div>
          ) : (
            <ul className="divide-y">
              {balances.map((b) => {
                const positive = Number(b.pending_total) > 0;
                return (
                  <li
                    key={b.beneficiary_id}
                    className="flex flex-wrap items-center justify-between gap-3 py-3"
                  >
                    <div className="min-w-0">
                      <p className="text-sm font-medium">{b.beneficiary_name}</p>
                      <p className="text-muted-foreground text-xs">
                        {b.pending_count} lançamento(s) a acertar · já pago {brl(b.paid_total)}
                      </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-3">
                      <span
                        className={cn(
                          "text-right text-sm font-semibold tabular-nums",
                          Number(b.pending_total) < 0 && "text-red-700",
                        )}
                      >
                        {brl(b.pending_total)}
                      </span>
                      {manage && positive && (
                        <ActionForm
                          action={settleAction.bind(null, tab.key, b.beneficiary_id)}
                          submitLabel="Gerar conta a pagar"
                          className="flex items-center gap-2 space-y-0"
                        >
                          <Input
                            name="due_date"
                            type="date"
                            defaultValue={inDays(7)}
                            aria-label="Vencimento"
                            className="w-40"
                          />
                        </ActionForm>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>

      {entries.length > 0 && (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Últimos lançamentos</CardTitle>
              <CardDescription>Base = valor da parcela recebido (sem multa e juros de mora).</CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <ul className="divide-y">
              {entries.map((e) => (
                <li key={e.id} className="flex flex-wrap items-baseline justify-between gap-2 py-2.5 text-sm">
                  <span>
                    <strong className={cn("tabular-nums", e.is_reversal && "text-red-700")}>
                      {brl(e.amount)}
                    </strong>{" "}
                    · {e.beneficiary_name} ·{" "}
                    <Link href={`/app/contratos/${e.contract_id}`} className="underline">
                      contrato {e.contract_number}
                    </Link>
                    <span className="text-muted-foreground">
                      {" "}
                      · {pct(e.pct)} de {brl(e.base)}
                      {e.is_reversal ? " · estorno" : e.on_signature ? " · na assinatura" : ""}
                    </span>
                  </span>
                  <span className="flex items-center gap-2">
                    {e.payable_id ? (
                      <Link href={`/app/financeiro/pagar/${e.payable_id}`}>
                        <Badge variant={e.payable_status === "paga" ? "default" : "warning"}>
                          {e.payable_status === "paga" ? "Pago" : "Em conta a pagar"}
                        </Badge>
                      </Link>
                    ) : (
                      <Badge variant="outline">A acertar</Badge>
                    )}
                    <span className="text-muted-foreground text-xs">{dateTime(e.created_at)}</span>
                  </span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
