import { ShieldAlert, ShieldCheck } from "lucide-react";
import type { Metadata } from "next";
import { EnableMfa, ManageMfa } from "@/components/account/mfa-manager";
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/primitives";
import { dateTime } from "@/lib/format";
import { api, getMe } from "@/lib/session";

export const metadata: Metadata = { title: "Segurança da conta" };

type MfaStatus = {
  enabled: boolean;
  available: boolean;
  recovery_codes_left: number;
  enabled_at: string | null;
};

export default async function SegurancaPage() {
  const [me, st] = await Promise.all([getMe(), api<MfaStatus>("/auth/mfa")]);
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-brand-institutional text-2xl font-bold">Segurança da conta</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          {me.full_name} · {me.email}. Vale para todas as empresas em que você tem acesso.
        </p>
      </div>

      <Card className={!st.enabled && me.mfa_recommended ? "border-amber-300" : undefined}>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle className="flex items-center gap-2">
                {st.enabled ? (
                  <ShieldCheck className="text-brand size-4" />
                ) : (
                  <ShieldAlert className="size-4 text-amber-600" />
                )}
                Verificação em duas etapas
              </CardTitle>
              <CardDescription>
                Além da senha, o login pede um código do aplicativo autenticador do seu celular. Quem
                descobrir sua senha não entra sem o celular.
              </CardDescription>
            </div>
            <Badge variant={st.enabled ? "default" : me.mfa_recommended ? "warning" : "muted"}>
              {st.enabled ? "Ligada" : "Desligada"}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {st.enabled ? (
            <>
              <p className="text-sm">
                Ligada {st.enabled_at && `em ${dateTime(st.enabled_at)}`} · {st.recovery_codes_left} código(s)
                de recuperação restante(s).
                {st.recovery_codes_left <= 3 && " Gere novos códigos."}
              </p>
              <ManageMfa />
            </>
          ) : !st.available ? (
            <p className="text-muted-foreground text-sm">
              Indisponível neste servidor no momento (falta a chave do cofre de credenciais). Fale com o
              suporte.
            </p>
          ) : (
            <>
              {me.mfa_recommended && (
                <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">
                  Recomendado para o seu perfil ({me.role_label}): você tem acesso a dinheiro e à equipe.
                </p>
              )}
              <EnableMfa />
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
