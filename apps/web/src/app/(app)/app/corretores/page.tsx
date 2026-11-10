import type { Metadata } from "next";
import { redirect } from "next/navigation";
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
import { createBrokerAction } from "@/lib/actions-fase2a";
import { api, can, getMe } from "@/lib/session";
import type { Broker } from "@/lib/types";
import { BrokerToggle } from "./broker-toggle";

export const metadata: Metadata = { title: "Corretores" };

export default async function CorretoresPage() {
  const me = await getMe();
  if (!can(me, "brokers:view")) redirect("/app");
  const brokers = await api<Broker[]>("/brokers");
  const manage = can(me, "brokers:manage");
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-brand-institutional text-2xl font-bold">Corretores</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Corretores internos e parceiros. Comissões chegam na Fase 3.
        </p>
      </div>
      <div className="grid gap-4 lg:grid-cols-[1fr_380px]">
        <Card>
          <CardContent>
            {brokers.length === 0 ? (
              <p className="text-muted-foreground text-sm">Nenhum corretor cadastrado.</p>
            ) : (
              <ul className="divide-y">
                {brokers.map((b) => (
                  <li key={b.id} className="flex flex-wrap items-center justify-between gap-2 py-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{b.name}</p>
                      <p className="text-muted-foreground truncate text-xs">
                        {[b.creci && `CRECI ${b.creci}`, b.email, b.phone].filter(Boolean).join(" · ") ||
                          "sem contato"}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      {!b.is_active && <Badge variant="muted">Inativo</Badge>}
                      {manage && <BrokerToggle brokerId={b.id} active={b.is_active} />}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
        {manage && (
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Novo corretor</CardTitle>
                <CardDescription>Corretor inativo não pode receber novas reservas.</CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              <ActionForm action={createBrokerAction} submitLabel="Cadastrar" resetOnSuccess>
                <Field label="Nome" htmlFor="name">
                  <Input id="name" name="name" required minLength={2} maxLength={160} />
                </Field>
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="CRECI" htmlFor="creci">
                    <Input id="creci" name="creci" maxLength={30} />
                  </Field>
                  <Field label="CPF ou CNPJ" htmlFor="document">
                    <Input id="document" name="document" inputMode="numeric" maxLength={20} />
                  </Field>
                </div>
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
