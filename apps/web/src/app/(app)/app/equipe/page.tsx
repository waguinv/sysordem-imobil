import type { Metadata } from "next";
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
import { addMemberAction } from "@/lib/actions-senha";
import { api, ApiError, can, getMe } from "@/lib/session";

export const metadata: Metadata = { title: "Equipe e acessos" };

type Member = {
  id: string;
  email: string;
  full_name: string;
  role: string;
  is_active: boolean;
  mfa_enabled: boolean;
};

const ROLE_LABEL: Record<string, string> = {
  owner: "Proprietário",
  admin: "Administrador",
  financeiro: "Financeiro",
  comercial: "Comercial",
  corretor: "Corretor",
  leitura: "Somente leitura",
};

export default async function EquipePage() {
  const me = await getMe();
  let members: Member[] | null = null;
  try {
    members = await api<Member[]>("/members");
  } catch (e) {
    if (!(e instanceof ApiError && e.status === 403)) throw e;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-brand-institutional text-2xl font-bold">Equipe e acessos</h1>
        <p className="text-muted-foreground mt-1 text-sm">Usuários com acesso a {me.tenant.name}.</p>
      </div>
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Membros</CardTitle>
            <CardDescription>Cada perfil tem permissões próprias, validadas no servidor.</CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          {members === null ? (
            <p className="text-muted-foreground text-sm">Seu perfil não tem permissão para ver a equipe.</p>
          ) : (
            <ul className="divide-y">
              {members.map((m) => (
                <li key={m.id} className="flex flex-wrap items-center justify-between gap-2 py-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{m.full_name}</p>
                    <p className="text-muted-foreground truncate text-xs">{m.email}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge>{ROLE_LABEL[m.role] ?? m.role}</Badge>
                    {!m.is_active && <Badge variant="muted">Inativo</Badge>}
                    {m.is_active && (
                      <Badge variant={m.mfa_enabled ? "outline" : "muted"}>
                        {m.mfa_enabled ? "2 etapas" : "Só senha"}
                      </Badge>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
      {can(me, "members:manage") && (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Adicionar pessoa</CardTitle>
              <CardDescription>
                {me.email_enabled
                  ? "A pessoa recebe um e-mail para criar a própria senha (o link vale 7 dias). Quem já usa o sistema em outra empresa só recebe o aviso do novo acesso."
                  : "O envio de e-mail não está ligado: defina uma senha inicial e entregue por um canal seguro."}
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <ActionForm action={addMemberAction} submitLabel="Adicionar" resetOnSuccess>
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Nome" htmlFor="nm-name">
                  <Input id="nm-name" name="full_name" required minLength={2} maxLength={160} />
                </Field>
                <Field label="E-mail" htmlFor="nm-email">
                  <Input id="nm-email" name="email" type="email" required maxLength={254} />
                </Field>
                <Field label="Perfil" htmlFor="nm-role">
                  <select id="nm-role" name="role" className={selectClass} defaultValue="comercial">
                    {Object.entries(ROLE_LABEL)
                      .filter(([r]) => r !== "owner" || me.role === "owner")
                      .map(([r, l]) => (
                        <option key={r} value={r}>
                          {l}
                        </option>
                      ))}
                  </select>
                </Field>
              </div>
              <Field
                label={me.email_enabled ? "Senha inicial (opcional — sem ela, vai convite)" : "Senha inicial"}
                htmlFor="nm-pw"
              >
                <Input
                  id="nm-pw"
                  name="initial_password"
                  type="password"
                  autoComplete="new-password"
                  minLength={12}
                  required={!me.email_enabled}
                  className="sm:max-w-sm"
                />
              </Field>
            </ActionForm>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
