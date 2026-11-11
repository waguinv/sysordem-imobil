import { ArrowLeft, Link2 } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ActionForm, Field } from "@/components/ui/action-form";
import {
  Badge,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Input,
} from "@/components/ui/primitives";
import {
  ignoreLineAction,
  reconcileLineAction,
  undoLineAction,
  unignoreLineAction,
} from "@/lib/actions-fase4b";
import { brl, dateBR, dateTime, LINE_STATUS, reconTargetHref } from "@/lib/format";
import { api, ApiError, can, getMe } from "@/lib/session";
import type { ReconTarget, StatementLineDetail } from "@/lib/types";

export const metadata: Metadata = { title: "Lançamento do extrato" };

function TargetRow({ t }: { t: ReconTarget }) {
  return (
    <span className="flex min-w-0 flex-1 flex-wrap items-baseline justify-between gap-2">
      <span className="min-w-0">
        <Link href={reconTargetHref(t)} className="font-medium hover:underline">
          {t.label}
        </Link>
        <span className="text-muted-foreground block text-xs">
          {t.kind === "recebimento" ? "Recebimento" : "Conta paga"} em {dateBR(t.on)} · {t.detail}
        </span>
      </span>
      <span className="tabular-nums">{brl(t.amount)}</span>
    </span>
  );
}

export default async function LinhaPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const me = await getMe();
  let ln: StatementLineDetail;
  try {
    ln = await api<StatementLineDetail>(`/statement-lines/${encodeURIComponent(id)}`);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 422 || e.status === 403)) notFound();
    throw e;
  }
  const manage = can(me, "finance:manage");
  const pending = ln.status === "pendente";
  const candidates = pending ? await api<ReconTarget[]>(`/statement-lines/${ln.id}/candidates`) : [];
  const st = LINE_STATUS[ln.status];
  const credit = Number(ln.amount) > 0;
  const history = ln.matches.filter((m) => m.undone_at);

  return (
    <div className="space-y-6">
      <Link
        href={`/app/financeiro/conciliacao?conta=${ln.bank_account_id}`}
        className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-sm"
      >
        <ArrowLeft className="size-4" /> {ln.account_name}
      </Link>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-brand-institutional text-2xl font-bold">{ln.description}</h1>
          <p className="text-muted-foreground text-sm">
            {credit ? "Crédito" : "Débito"} de {brl(ln.amount)} em {dateBR(ln.posted_on)} · arquivo{" "}
            {ln.statement_file}
          </p>
        </div>
        <Badge variant={st?.badge ?? "muted"}>{st?.label ?? ln.status}</Badge>
      </div>

      {ln.status === "conciliada" && (
        <Card>
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-2">
                <Link2 className="text-brand size-4" /> Conciliado com
              </CardTitle>
              <CardDescription>
                {ln.matches.find((m) => !m.undone_at)?.auto ? "Automaticamente" : "Manualmente"}
                {ln.matches.find((m) => !m.undone_at)?.created_by_name &&
                  ` por ${ln.matches.find((m) => !m.undone_at)?.created_by_name}`}
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <ul className="divide-y text-sm">
              {ln.targets.map((t) => (
                <li key={t.id} className="flex py-2">
                  <TargetRow t={t} />
                </li>
              ))}
            </ul>
            {can(me, "finance:reverse") && (
              <details>
                <summary className="text-brand-institutional cursor-pointer text-xs font-medium">
                  Desfazer conciliação
                </summary>
                <ActionForm
                  action={undoLineAction.bind(null, ln.id)}
                  submitLabel="Desfazer"
                  submitVariant="outline"
                  className="mt-2 sm:max-w-sm"
                >
                  <Field label="Motivo" htmlFor="undo-reason">
                    <Input id="undo-reason" name="reason" required minLength={3} maxLength={500} />
                  </Field>
                </ActionForm>
              </details>
            )}
          </CardContent>
        </Card>
      )}

      {ln.status === "ignorada" && (
        <Card>
          <CardContent className="space-y-3">
            <p className="text-sm">
              Ignorado: <strong>{ln.ignored_reason}</strong>
            </p>
            {manage && (
              <ActionForm
                action={unignoreLineAction.bind(null, ln.id)}
                submitLabel="Voltar a pendente"
                submitVariant="outline"
              >
                {null}
              </ActionForm>
            )}
          </CardContent>
        </Card>
      )}

      {pending && manage && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Conciliar</CardTitle>
                <CardDescription>
                  {credit ? "Recebimentos" : "Contas pagas"} de até {brl(String(Math.abs(Number(ln.amount))))}{" "}
                  a até 15 dias. Marque um ou mais — a soma precisa bater com o extrato.
                </CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              {candidates.length === 0 ? (
                <p className="text-muted-foreground text-sm">
                  Nenhum lançamento compatível. Registre o {credit ? "recebimento" : "pagamento"} no sistema
                  ou ignore esta linha (tarifa, transferência entre contas…).
                </p>
              ) : (
                <ActionForm
                  action={reconcileLineAction.bind(null, ln.id)}
                  submitLabel="Conciliar selecionados"
                >
                  <ul className="divide-y text-sm">
                    {candidates.map((t) => (
                      <li key={t.id} className="flex items-start gap-3 py-2">
                        <input
                          type="checkbox"
                          name="target_ids"
                          value={t.id}
                          defaultChecked={t.exact && candidates.filter((c) => c.exact).length === 1}
                          aria-label={`${t.label} ${brl(t.amount)}`}
                          className="mt-1 size-4"
                        />
                        <TargetRow t={t} />
                        {t.exact && <Badge variant="outline">valor exato</Badge>}
                      </li>
                    ))}
                  </ul>
                </ActionForm>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Ignorar</CardTitle>
                <CardDescription>Para o que não tem lançamento no sistema.</CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              <ActionForm
                action={ignoreLineAction.bind(null, ln.id)}
                submitLabel="Ignorar"
                submitVariant="outline"
              >
                <Field label="Motivo" htmlFor="ignore-reason">
                  <Input
                    id="ignore-reason"
                    name="reason"
                    required
                    minLength={3}
                    maxLength={500}
                    placeholder="ex.: tarifa bancária"
                  />
                </Field>
              </ActionForm>
            </CardContent>
          </Card>
        </div>
      )}

      {history.length > 0 && (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Conciliações desfeitas</CardTitle>
              <CardDescription>
                Nada é apagado: fica o registro de quem conciliou e por que foi desfeito.
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <ul className="divide-y text-sm">
              {history.map((m) => (
                <li key={m.id} className="py-2">
                  {m.kind === "recebimento" ? "Recebimento" : "Conta paga"} de {brl(m.amount)} ·{" "}
                  {m.auto ? "automática" : (m.created_by_name ?? "—")} em {dateTime(m.created_at)}
                  <span className="text-muted-foreground block text-xs">
                    Desfeita {m.undone_at && dateTime(m.undone_at)} · {m.undo_reason}
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
