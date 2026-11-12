import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { ActionForm, Field } from "@/components/ui/action-form";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Input } from "@/components/ui/primitives";
import { createLandOwnerAction } from "@/lib/actions-fase1";
import { maskDoc } from "@/lib/format";
import { api, can, getMe } from "@/lib/session";
import type { LandOwner } from "@/lib/types";

export const metadata: Metadata = { title: "Proprietários da área" };

export default async function ProprietariosPage() {
  const me = await getMe();
  if (!can(me, "owners:view")) redirect("/app");
  const owners = await api<LandOwner[]>("/land-owners");
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-brand-institutional text-2xl font-bold">Proprietários da área</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Donos das glebas dos empreendimentos. Dados pessoais visíveis só a perfis autorizados.
        </p>
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
        <Card>
          <CardContent>
            {owners.length === 0 ? (
              <p className="text-muted-foreground text-sm">Nenhum proprietário cadastrado.</p>
            ) : (
              <ul className="divide-y">
                {owners.map((o) => (
                  <li key={o.id} className="flex flex-wrap items-center justify-between gap-2 py-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{o.name}</p>
                      <p className="text-muted-foreground truncate text-xs">
                        {[o.email, o.phone].filter(Boolean).join(" · ") || "sem contato"}
                      </p>
                    </div>
                    <span className="text-muted-foreground text-xs">{maskDoc(o.document)}</span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
        {can(me, "owners:manage") && (
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Novo proprietário</CardTitle>
                <CardDescription>Acordos financeiros com o proprietário chegam na Fase 3.</CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              <ActionForm action={createLandOwnerAction} submitLabel="Cadastrar" resetOnSuccess>
                <Field label="Nome" htmlFor="name">
                  <Input id="name" name="name" required minLength={2} maxLength={160} />
                </Field>
                <Field label="CPF ou CNPJ" htmlFor="document">
                  <Input id="document" name="document" inputMode="numeric" maxLength={20} />
                </Field>
                <Field label="E-mail" htmlFor="email">
                  <Input id="email" name="email" type="email" maxLength={254} />
                </Field>
                <Field label="Telefone" htmlFor="phone">
                  <Input id="phone" name="phone" type="tel" maxLength={30} />
                </Field>
              </ActionForm>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
