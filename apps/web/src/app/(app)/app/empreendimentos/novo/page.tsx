import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { ActionForm, Field, selectClass } from "@/components/ui/action-form";
import { Card, CardContent, Input } from "@/components/ui/primitives";
import { createDevelopmentAction } from "@/lib/actions-fase1";
import { DEV_KIND, DEV_STATUS } from "@/lib/format";
import { api, can, getMe } from "@/lib/session";
import type { LandOwner } from "@/lib/types";

export const metadata: Metadata = { title: "Novo empreendimento" };

export default async function NovoEmpreendimentoPage() {
  const me = await getMe();
  if (!can(me, "developments:manage")) redirect("/app/empreendimentos");
  const owners = can(me, "owners:view") ? await api<LandOwner[]>("/land-owners") : [];
  return (
    <div className="max-w-2xl space-y-6">
      <h1 className="text-brand-institutional text-2xl font-bold">Novo empreendimento</h1>
      <Card>
        <CardContent>
          <ActionForm action={createDevelopmentAction} submitLabel="Criar empreendimento">
            <Field label="Nome" htmlFor="name">
              <Input id="name" name="name" required minLength={2} maxLength={160} />
            </Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Tipo" htmlFor="kind">
                <select id="kind" name="kind" className={selectClass} defaultValue="loteamento">
                  {Object.entries(DEV_KIND).map(([v, l]) => (
                    <option key={v} value={v}>
                      {l}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Situação" htmlFor="status">
                <select id="status" name="status" className={selectClass} defaultValue="planejamento">
                  {Object.entries(DEV_STATUS).map(([v, l]) => (
                    <option key={v} value={v}>
                      {l}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <div className="grid gap-3 sm:grid-cols-[1fr_90px]">
              <Field label="Cidade" htmlFor="city">
                <Input id="city" name="city" maxLength={120} />
              </Field>
              <Field label="UF" htmlFor="state">
                <Input id="state" name="state" maxLength={2} className="uppercase" />
              </Field>
            </div>
            <Field label="Endereço / acesso" htmlFor="address">
              <Input id="address" name="address" maxLength={240} />
            </Field>
            {owners.length > 0 && (
              <Field label="Proprietário da área" htmlFor="land_owner_id">
                <select id="land_owner_id" name="land_owner_id" className={selectClass} defaultValue="">
                  <option value="">— não informado —</option>
                  {owners.map((o) => (
                    <option key={o.id} value={o.id}>
                      {o.name}
                    </option>
                  ))}
                </select>
              </Field>
            )}
            <Field label="Descrição" htmlFor="description">
              <textarea
                id="description"
                name="description"
                rows={3}
                maxLength={4000}
                className={`${selectClass} h-auto py-2`}
              />
            </Field>
          </ActionForm>
        </CardContent>
      </Card>
    </div>
  );
}
