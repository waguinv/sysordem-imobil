import { Search } from "lucide-react";
import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { ActionForm, Field } from "@/components/ui/action-form";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Input } from "@/components/ui/primitives";
import { createCustomerAction } from "@/lib/actions-fase2a";
import { maskDoc } from "@/lib/format";
import { api, can, getMe } from "@/lib/session";
import type { Customer } from "@/lib/types";

export const metadata: Metadata = { title: "Compradores" };

export default async function CompradoresPage({ searchParams }: { searchParams: Promise<{ q?: string }> }) {
  const me = await getMe();
  if (!can(me, "customers:view")) redirect("/app");
  const { q = "" } = await searchParams;
  const customers = await api<Customer[]>(`/customers${q ? `?q=${encodeURIComponent(q)}` : ""}`);
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-brand-institutional text-2xl font-bold">Compradores</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Clientes de {me.tenant.name}. Dados pessoais visíveis só a perfis autorizados.
        </p>
      </div>
      <div className="grid gap-4 lg:grid-cols-[1fr_380px]">
        <Card>
          <CardContent className="space-y-3">
            <form className="relative" role="search">
              <Search className="text-muted-foreground absolute top-3 left-3 size-4" />
              <Input
                name="q"
                defaultValue={q}
                placeholder="Buscar por nome, e-mail ou CPF/CNPJ"
                className="pl-9"
              />
            </form>
            {customers.length === 0 ? (
              <p className="text-muted-foreground py-4 text-sm">
                {q ? "Nenhum comprador encontrado." : "Nenhum comprador cadastrado."}
              </p>
            ) : (
              <ul className="divide-y">
                {customers.map((c) => (
                  <li key={c.id} className="flex flex-wrap items-center justify-between gap-2 py-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{c.name}</p>
                      <p className="text-muted-foreground truncate text-xs">
                        {[c.email, c.phone, c.city && `${c.city}${c.state ? `/${c.state}` : ""}`]
                          .filter(Boolean)
                          .join(" · ") || "sem contato"}
                      </p>
                    </div>
                    <span className="text-muted-foreground text-xs">{maskDoc(c.document)}</span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
        {can(me, "customers:manage") && (
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Novo comprador</CardTitle>
                <CardDescription>Peça só os dados necessários para a venda.</CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              <ActionForm action={createCustomerAction} submitLabel="Cadastrar" resetOnSuccess>
                <Field label="Nome" htmlFor="name">
                  <Input id="name" name="name" required minLength={2} maxLength={160} />
                </Field>
                <Field label="CPF ou CNPJ" htmlFor="document">
                  <Input id="document" name="document" inputMode="numeric" maxLength={20} />
                </Field>
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="E-mail" htmlFor="email">
                    <Input id="email" name="email" type="email" maxLength={254} />
                  </Field>
                  <Field label="Telefone" htmlFor="phone">
                    <Input id="phone" name="phone" type="tel" maxLength={30} />
                  </Field>
                </div>
                <div className="grid grid-cols-[1fr_80px] gap-3">
                  <Field label="Cidade" htmlFor="city">
                    <Input id="city" name="city" maxLength={120} />
                  </Field>
                  <Field label="UF" htmlFor="state">
                    <Input id="state" name="state" maxLength={2} className="uppercase" />
                  </Field>
                </div>
              </ActionForm>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
