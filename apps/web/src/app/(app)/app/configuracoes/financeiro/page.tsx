import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { ActionForm, Field, selectClass } from "@/components/ui/action-form";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Input } from "@/components/ui/primitives";
import { saveFinanceSettingsAction, saveOwnerShareAction } from "@/lib/actions-fase3b";
import { COMMISSION_BASIS } from "@/lib/format";
import { api, can, getMe } from "@/lib/session";
import type { FinanceSettings, OwnerShare } from "@/lib/types";

export const metadata: Metadata = { title: "Regras de comissão" };

const br = (v: string) => v.replace(".", ",");

export default async function RegrasFinanceirasPage() {
  const me = await getMe();
  if (!can(me, "settings:manage")) redirect("/app");
  const [settings, shares] = await Promise.all([
    api<FinanceSettings>("/settings/finance"),
    can(me, "owners:view") ? api<OwnerShare[]>("/owner-shares") : Promise.resolve([] as OwnerShare[]),
  ]);
  const editShares = can(me, "owners:manage");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-brand-institutional text-2xl font-bold">Regras de comissão e repasse</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          As regras são congeladas em cada contrato na assinatura: mudanças valem para os próximos. Hipóteses
          a validar com o contador.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Comissão de corretores</CardTitle>
              <CardDescription>
                {settings.is_default ? "Usando os padrões do sistema." : "Regras próprias da empresa."} O
                percentual de cada corretor pode ser definido em Corretores.
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <ActionForm action={saveFinanceSettingsAction} submitLabel="Salvar regras">
              <Field label="Quando e sobre o quê" htmlFor="commission_basis">
                <select
                  id="commission_basis"
                  name="commission_basis"
                  className={selectClass}
                  defaultValue={settings.commission_basis}
                >
                  {Object.entries(COMMISSION_BASIS).map(([v, b]) => (
                    <option key={v} value={v}>
                      {b.label} — {b.hint}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Percentual padrão (%)" htmlFor="default_commission_pct">
                <Input
                  id="default_commission_pct"
                  name="default_commission_pct"
                  inputMode="decimal"
                  defaultValue={br(settings.default_commission_pct)}
                  required
                />
              </Field>
              <Field label="Motivo da alteração" htmlFor="fs-reason">
                <Input id="fs-reason" name="reason" required minLength={3} maxLength={500} />
              </Field>
            </ActionForm>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>Repasse ao proprietário da área</CardTitle>
              <CardDescription>
                % de cada parcela recebida que vai para o proprietário (permuta financeira). Exige o
                proprietário definido no empreendimento.
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            {shares.length === 0 ? (
              <p className="text-muted-foreground text-sm">Nenhum empreendimento cadastrado.</p>
            ) : (
              <ul className="divide-y">
                {shares.map((s) => (
                  <li key={s.development_id} className="space-y-2 py-3">
                    <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
                      <span className="font-medium">{s.development_name}</span>
                      <span className="text-muted-foreground text-xs">
                        {s.land_owner_name ?? "sem proprietário"} · {br(s.owner_share_pct)}%
                      </span>
                    </div>
                    {editShares && s.land_owner_id && (
                      <ActionForm
                        action={saveOwnerShareAction.bind(null, s.development_id)}
                        submitLabel="Salvar"
                        submitVariant="outline"
                        className="grid grid-cols-[90px_minmax(0,1fr)_auto] items-end gap-2 space-y-0 [&>p]:col-span-3"
                      >
                        <Input
                          name="owner_share_pct"
                          inputMode="decimal"
                          defaultValue={br(s.owner_share_pct)}
                          aria-label={`Repasse de ${s.development_name} (%)`}
                        />
                        <Input
                          name="reason"
                          placeholder="motivo"
                          required
                          minLength={3}
                          aria-label="Motivo"
                        />
                      </ActionForm>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
