import type { Metadata } from "next";
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/primitives";
import { api, ApiError, getMe } from "@/lib/session";

export const metadata: Metadata = { title: "Equipe e acessos" };

type Member = { id: string; email: string; full_name: string; role: string; is_active: boolean };

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
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
