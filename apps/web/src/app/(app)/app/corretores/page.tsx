import type { Metadata } from "next";
import { redirect } from "next/navigation";
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
import { createBrokerAction } from "@/lib/actions-fase2a";
import { linkBrokerUserAction } from "@/lib/actions-fase2c";
import { setBrokerCommissionAction } from "@/lib/actions-fase3b";
import { api, can, getMe } from "@/lib/session";
import type { Broker, Member } from "@/lib/types";
import { BrokerToggle } from "./broker-toggle";

export const metadata: Metadata = { title: "Corretores" };

export default async function CorretoresPage() {
  const me = await getMe();
  if (!can(me, "brokers:view")) redirect("/app");
  const manage = can(me, "brokers:manage");
  const [brokers, members] = await Promise.all([
    api<Broker[]>("/brokers"),
    can(me, "members:view") ? api<Member[]>("/members") : Promise.resolve([] as Member[]),
  ]);
  const userName = new Map(members.map((m) => [m.user_id, m.full_name]));
  const brokerUsers = members.filter((m) => m.role === "corretor" && m.is_active);
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-brand-institutional text-2xl font-bold">Corretores</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Corretores internos e parceiros. Quem tem acesso ao sistema com o perfil Corretor vê só os próprios
          leads, clientes, propostas e reservas. Comissões chegam na Fase 3.
        </p>
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
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
                        {[
                          b.creci && `CRECI ${b.creci}`,
                          b.email,
                          b.phone,
                          b.user_id && `acesso: ${userName.get(b.user_id) ?? "usuário vinculado"}`,
                          `comissão ${b.commission_pct ? `${b.commission_pct.replace(".", ",")}%` : "padrão"}`,
                        ]
                          .filter(Boolean)
                          .join(" · ") || "sem contato"}
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
          <div className="space-y-4">
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
                  <Field label="Comissão (%) — vazio usa o padrão da empresa" htmlFor="commission_pct">
                    <Input id="commission_pct" name="commission_pct" inputMode="decimal" maxLength={6} />
                  </Field>
                </ActionForm>
              </CardContent>
            </Card>
            {brokers.length > 0 && (
              <Card>
                <CardHeader>
                  <div>
                    <CardTitle>Comissão do corretor</CardTitle>
                    <CardDescription>
                      Vale para contratos assinados a partir de agora. Vazio = padrão da empresa.
                    </CardDescription>
                  </div>
                </CardHeader>
                <CardContent>
                  <ActionForm action={setBrokerCommissionAction} submitLabel="Salvar comissão">
                    <div className="grid grid-cols-[minmax(0,1fr)_100px] gap-3">
                      <Field label="Corretor" htmlFor="comm-broker">
                        <select id="comm-broker" name="broker_id" className={selectClass} required>
                          {brokers.map((b) => (
                            <option key={b.id} value={b.id}>
                              {b.name}
                            </option>
                          ))}
                        </select>
                      </Field>
                      <Field label="%" htmlFor="comm-pct">
                        <Input id="comm-pct" name="commission_pct" inputMode="decimal" maxLength={6} />
                      </Field>
                    </div>
                  </ActionForm>
                </CardContent>
              </Card>
            )}
            {brokers.length > 0 && (
              <Card>
                <CardHeader>
                  <div>
                    <CardTitle>Acesso do corretor</CardTitle>
                    <CardDescription>
                      Ligue o cadastro ao usuário da equipe com perfil Corretor (crie o usuário em Equipe e
                      acessos).
                    </CardDescription>
                  </div>
                </CardHeader>
                <CardContent>
                  <ActionForm action={linkBrokerUserAction} submitLabel="Salvar acesso">
                    <Field label="Corretor" htmlFor="link-broker">
                      <select id="link-broker" name="broker_id" className={selectClass} required>
                        {brokers.map((b) => (
                          <option key={b.id} value={b.id}>
                            {b.name}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <Field label="Usuário" htmlFor="link-user">
                      <select id="link-user" name="user_id" className={selectClass} defaultValue="">
                        <option value="">— sem acesso ao sistema —</option>
                        {brokerUsers.map((m) => (
                          <option key={m.user_id} value={m.user_id}>
                            {m.full_name} ({m.email})
                          </option>
                        ))}
                      </select>
                    </Field>
                  </ActionForm>
                </CardContent>
              </Card>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
