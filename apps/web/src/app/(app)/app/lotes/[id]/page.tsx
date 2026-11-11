import { ArrowLeft, FileSignature, History } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ProposalPanel } from "@/components/lots/proposal-panel";
import { ReservationPanel } from "@/components/lots/reservation-panel";
import { ActionForm, Field, selectClass } from "@/components/ui/action-form";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Input } from "@/components/ui/primitives";
import { changeLotStatusAction, updateLotAction } from "@/lib/actions-fase1";
import { brl, dateTime, LOT_STATUS, m2 } from "@/lib/format";
import { api, ApiError, can, getMe } from "@/lib/session";
import type { LotDetail, LotHistory } from "@/lib/types";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Lote" };

const toInput = (v: string | null) => (v == null ? "" : v.replace(".", ","));

export default async function LotePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const me = await getMe();
  let lot: LotDetail;
  let history: LotHistory[];
  try {
    const enc = encodeURIComponent(id);
    [lot, history] = await Promise.all([
      api<LotDetail>(`/lots/${enc}`),
      api<LotHistory[]>(`/lots/${enc}/history`),
    ]);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 422)) notFound();
    throw e;
  }
  const s = LOT_STATUS[lot.status] ?? LOT_STATUS.disponivel!;
  const canStatus = can(me, "lots:status") && lot.allowed_transitions.length > 0;
  const canEdit = can(me, "developments:manage");

  return (
    <div className="space-y-6">
      <Link
        href={`/app/empreendimentos/${lot.development_id}`}
        className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-sm"
      >
        <ArrowLeft className="size-4" /> {lot.development_name}
      </Link>

      <div className="flex flex-wrap items-center gap-4">
        <div className={cn("grid size-16 place-items-center rounded-xl text-center", s.cell)}>
          <div>
            <small className="block text-[10px] opacity-80">Q{lot.block_code}</small>
            <strong className="text-xl leading-none">{lot.number}</strong>
          </div>
        </div>
        <div>
          <h1 className="text-brand-institutional text-2xl font-bold">
            Quadra {lot.block_code} · Lote {lot.number}
          </h1>
          <p className="text-muted-foreground text-sm">
            {s.label} · {m2(lot.area_m2)}
            {lot.front_m && ` · testada ${lot.front_m.replace(".", ",")} m`} · {brl(lot.price)}
          </p>
        </div>
      </div>

      {lot.contract_id && can(me, "contracts:view") && (
        <Link
          href={`/app/contratos/${lot.contract_id}`}
          className="bg-brand/10 text-brand-institutional hover:bg-brand/15 flex items-center gap-2 rounded-xl px-4 py-3 text-sm font-medium"
        >
          <FileSignature className="size-4" />
          {lot.status === "vendido"
            ? "Ver contrato de venda"
            : "Contrato em rascunho — abrir para assinar ou ajustar"}
        </Link>
      )}

      <ReservationPanel lot={lot} me={me} />
      <ProposalPanel lot={lot} me={me} />

      <div className="grid gap-4 lg:grid-cols-2">
        {canStatus && (
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Mudar status</CardTitle>
                <CardDescription>A mudança fica no histórico com seu nome e o motivo.</CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              <ActionForm
                action={changeLotStatusAction.bind(null, lot.id, lot.version)}
                submitLabel="Confirmar"
              >
                <Field label="Novo status" htmlFor="to_status">
                  <select id="to_status" name="to_status" className={selectClass} required>
                    {lot.allowed_transitions.map((t) => (
                      <option key={t} value={t}>
                        {LOT_STATUS[t]?.label ?? t}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Motivo" htmlFor="reason">
                  <Input id="reason" name="reason" required minLength={3} maxLength={500} />
                </Field>
              </ActionForm>
            </CardContent>
          </Card>
        )}
        {lot.status === "vendido" && (
          <Card>
            <CardContent>
              <p className="text-muted-foreground text-sm">
                Lote vendido: o status só muda por distrato (Fase 3) e o preço não pode ser alterado.
              </p>
            </CardContent>
          </Card>
        )}

        {canEdit && (
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Dados do lote</CardTitle>
              </div>
            </CardHeader>
            <CardContent>
              <ActionForm action={updateLotAction.bind(null, lot.id, lot.version)} submitLabel="Salvar">
                <div className="grid gap-3 sm:grid-cols-3">
                  <Field label="Preço (R$)" htmlFor="price">
                    <Input
                      id="price"
                      name="price"
                      inputMode="decimal"
                      defaultValue={toInput(lot.price)}
                      readOnly={lot.status === "vendido"}
                      required
                    />
                  </Field>
                  <Field label="Área (m²)" htmlFor="area_m2">
                    <Input
                      id="area_m2"
                      name="area_m2"
                      inputMode="decimal"
                      defaultValue={toInput(lot.area_m2)}
                      required
                    />
                  </Field>
                  <Field label="Testada (m)" htmlFor="front_m">
                    <Input
                      id="front_m"
                      name="front_m"
                      inputMode="decimal"
                      defaultValue={toInput(lot.front_m)}
                    />
                  </Field>
                </div>
                <Field label="Observações" htmlFor="notes">
                  <Input id="notes" name="notes" defaultValue={lot.notes ?? ""} maxLength={2000} />
                </Field>
                <Field label="Motivo da alteração" htmlFor="edit-reason">
                  <Input id="edit-reason" name="reason" required minLength={3} maxLength={500} />
                </Field>
              </ActionForm>
            </CardContent>
          </Card>
        )}
      </div>

      <Card>
        <CardHeader>
          <div>
            <CardTitle className="flex items-center gap-2">
              <History className="text-brand size-4" /> Histórico de status
            </CardTitle>
          </div>
        </CardHeader>
        <CardContent>
          <ol className="divide-y">
            {history.map((h) => (
              <li key={h.id} className="flex flex-wrap items-baseline justify-between gap-2 py-2.5 text-sm">
                <span>
                  {h.from_status ? `${LOT_STATUS[h.from_status]?.label} → ` : ""}
                  <strong>{LOT_STATUS[h.to_status]?.label ?? h.to_status}</strong>
                  {h.reason && <span className="text-muted-foreground"> · {h.reason}</span>}
                </span>
                <span className="text-muted-foreground text-xs">
                  {h.actor_name ?? "sistema"} · {dateTime(h.created_at)}
                </span>
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>
    </div>
  );
}
