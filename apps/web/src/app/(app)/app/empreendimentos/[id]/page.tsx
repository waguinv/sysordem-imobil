import { ArrowLeft } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { LotMap, StatusLegend } from "@/components/lots/lot-map";
import { ActionForm, Field, selectClass } from "@/components/ui/action-form";
import {
  Badge,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Input,
} from "@/components/ui/primitives";
import { createBlockAction, createLotsBatchAction, updateDevelopmentStatusAction } from "@/lib/actions-fase1";
import { brl, DEV_KIND, DEV_STATUS } from "@/lib/format";
import { api, ApiError, can, getMe } from "@/lib/session";
import type { DevelopmentMap } from "@/lib/types";

export const metadata: Metadata = { title: "Empreendimento" };

export default async function EmpreendimentoPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const me = await getMe();
  let map: DevelopmentMap;
  try {
    map = await api<DevelopmentMap>(`/developments/${encodeURIComponent(id)}/map`);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 422)) notFound();
    throw e;
  }
  const dev = map.development;
  const manage = can(me, "developments:manage");

  return (
    <div className="space-y-6">
      <Link
        href="/app/empreendimentos"
        className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-sm"
      >
        <ArrowLeft className="size-4" /> Empreendimentos
      </Link>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-brand-institutional text-2xl font-bold">{dev.name}</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            {DEV_KIND[dev.kind] ?? dev.kind}
            {dev.city && ` · ${dev.city}${dev.state ? ` / ${dev.state}` : ""}`}
            {dev.address && ` · ${dev.address}`}
          </p>
        </div>
        <Badge variant={dev.status === "em_vendas" ? "default" : "muted"}>{DEV_STATUS[dev.status]}</Badge>
      </div>

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="Resumo">
        {[
          ["Lotes", String(dev.lots_total)],
          ["Disponíveis", String(dev.counts.disponivel)],
          ["Vendidos", String(dev.counts.vendido)],
          ["Estoque disponível", brl(dev.available_value)],
        ].map(([label, value]) => (
          <Card key={label} className="gap-1 py-4">
            <CardContent className="px-4">
              <p className="text-muted-foreground text-xs sm:text-sm">{label}</p>
              <p className="text-brand-institutional mt-1 text-base font-bold whitespace-nowrap tabular-nums sm:text-xl xl:text-2xl">
                {value}
              </p>
            </CardContent>
          </Card>
        ))}
      </section>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Mapa de lotes</CardTitle>
            <CardDescription>Toque em um lote para ver detalhes, histórico e mudar o status.</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <StatusLegend counts={dev.counts} />
          <LotMap map={map} />
        </CardContent>
      </Card>

      {manage && (
        <div className="grid gap-4 lg:grid-cols-3">
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Nova quadra</CardTitle>
                <CardDescription>Código curto: A, B, 1, 2…</CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              <ActionForm
                action={createBlockAction.bind(null, dev.id)}
                submitLabel="Criar quadra"
                resetOnSuccess
              >
                <Field label="Código" htmlFor="code">
                  <Input id="code" name="code" required maxLength={20} pattern="[0-9A-Za-z][0-9A-Za-z\-]*" />
                </Field>
              </ActionForm>
            </CardContent>
          </Card>

          <Card className="lg:col-span-2">
            <CardHeader>
              <div>
                <CardTitle>Adicionar lotes</CardTitle>
                <CardDescription>
                  Cria lotes numerados em sequência, com a mesma área e preço.
                </CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              {map.blocks.length === 0 ? (
                <p className="text-muted-foreground text-sm">Crie uma quadra primeiro.</p>
              ) : (
                <ActionForm
                  action={createLotsBatchAction.bind(null, dev.id)}
                  submitLabel="Criar lotes"
                  className="grid gap-3 sm:grid-cols-3 [&>button]:sm:col-span-3 [&>button]:sm:w-fit [&>p]:sm:col-span-3"
                >
                  <Field label="Quadra" htmlFor="block_id">
                    <select id="block_id" name="block_id" className={selectClass} required>
                      {map.blocks.map((b) => (
                        <option key={b.id} value={b.id}>
                          Quadra {b.code}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Do número" htmlFor="first">
                    <Input
                      id="first"
                      name="first"
                      type="number"
                      min={1}
                      max={9999}
                      defaultValue={1}
                      required
                    />
                  </Field>
                  <Field label="Até o número" htmlFor="last">
                    <Input
                      id="last"
                      name="last"
                      type="number"
                      min={1}
                      max={9999}
                      defaultValue={10}
                      required
                    />
                  </Field>
                  <Field label="Área (m²)" htmlFor="area_m2">
                    <Input id="area_m2" name="area_m2" inputMode="decimal" placeholder="300,00" required />
                  </Field>
                  <Field label="Testada (m)" htmlFor="front_m">
                    <Input id="front_m" name="front_m" inputMode="decimal" placeholder="12,00" />
                  </Field>
                  <Field label="Preço (R$)" htmlFor="price">
                    <Input id="price" name="price" inputMode="decimal" placeholder="150.000,00" required />
                  </Field>
                </ActionForm>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <div>
                <CardTitle>Situação do empreendimento</CardTitle>
              </div>
            </CardHeader>
            <CardContent>
              <ActionForm action={updateDevelopmentStatusAction.bind(null, dev.id)} submitLabel="Salvar">
                <Field label="Situação" htmlFor="dev-status">
                  <select id="dev-status" name="status" className={selectClass} defaultValue={dev.status}>
                    {Object.entries(DEV_STATUS).map(([v, l]) => (
                      <option key={v} value={v}>
                        {l}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Motivo" htmlFor="dev-reason">
                  <Input id="dev-reason" name="reason" placeholder="ex.: início das vendas" />
                </Field>
              </ActionForm>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
