"use client";

import { Loader2, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { useActionState } from "react";
import { CopyButton } from "@/components/ui/copy-button";
import { Button, Input, Label } from "@/components/ui/primitives";
import {
  confirmMfaAction,
  disableMfaAction,
  type MfaState,
  newRecoveryCodesAction,
  startMfaAction,
} from "@/lib/actions-mfa";

function Message({ state }: { state: MfaState }) {
  if (state.error)
    return (
      <p role="alert" className="text-destructive text-sm">
        {state.error}
      </p>
    );
  if (state.ok)
    return (
      <p role="status" className="text-brand-institutional text-sm">
        {state.ok}
      </p>
    );
  return null;
}

function Submit({ pending, children }: { pending: boolean; children: React.ReactNode }) {
  return (
    <Button type="submit" disabled={pending}>
      {pending && <Loader2 className="animate-spin" />} {children}
    </Button>
  );
}

/** Mostra os códigos de recuperação uma única vez. */
export function RecoveryCodes({ codes }: { codes: string[] }) {
  const router = useRouter();
  return (
    <div className="space-y-3 rounded-lg border border-amber-300 bg-amber-50 p-4">
      <p className="text-sm font-semibold text-amber-900">
        Guarde estes códigos agora — eles não aparecem de novo.
      </p>
      <p className="text-xs text-amber-900">
        Cada um vale uma vez e entra no lugar do código do aplicativo se você perder o celular. Guarde num
        gerenciador de senhas ou impresso, longe do celular.
      </p>
      <ul className="grid grid-cols-2 gap-1 font-mono text-sm sm:grid-cols-5">
        {codes.map((c) => (
          <li key={c} className="rounded bg-white px-2 py-1 text-center">
            {c}
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap gap-2">
        <CopyButton text={codes.join("\n")} label="Copiar códigos" />
        <Button type="button" size="sm" onClick={() => router.refresh()}>
          Já guardei os códigos
        </Button>
      </div>
    </div>
  );
}

function PasswordAndCode({ prefix }: { prefix: string }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="space-y-2">
        <Label htmlFor={`${prefix}-pw`}>Senha atual</Label>
        <Input id={`${prefix}-pw`} name="password" type="password" autoComplete="current-password" required />
      </div>
      <div className="space-y-2">
        <Label htmlFor={`${prefix}-code`}>Código do aplicativo ou de recuperação</Label>
        <Input id={`${prefix}-code`} name="code" autoComplete="one-time-code" maxLength={20} required />
      </div>
    </div>
  );
}

export function EnableMfa() {
  const [start, startAction, starting] = useActionState<MfaState, FormData>(startMfaAction, {});
  const [done, confirmAction, confirming] = useActionState<MfaState, FormData>(confirmMfaAction, {});

  if (done.codes) {
    return (
      <div className="space-y-4">
        <p className="text-brand-institutional flex items-center gap-2 font-medium">
          <ShieldCheck className="size-5" /> Verificação em duas etapas ligada.
        </p>
        <RecoveryCodes codes={done.codes} />
      </div>
    );
  }

  if (start.secret) {
    const grouped = start.secret.match(/.{1,4}/g)?.join(" ") ?? start.secret;
    return (
      <form action={confirmAction} className="space-y-4">
        <ol className="list-decimal space-y-3 pl-5 text-sm">
          <li>
            Instale um aplicativo autenticador no celular (Google Authenticator, Microsoft Authenticator,
            Authy ou o gerenciador de senhas).
          </li>
          <li className="space-y-2">
            <span>
              No aplicativo, escolha <strong>adicionar conta → inserir chave</strong> e digite esta chave
              (tipo: baseada em tempo):
            </span>
            <code className="bg-muted block rounded-md p-2 text-sm tracking-wider break-all select-all">
              {grouped}
            </code>
            <div className="flex flex-wrap gap-2">
              <CopyButton text={start.secret} label="Copiar chave" />
              {start.otpauthUri && (
                <a
                  href={start.otpauthUri}
                  className="text-brand-institutional inline-flex h-8 items-center text-sm underline"
                >
                  Abrir no aplicativo (no celular)
                </a>
              )}
            </div>
          </li>
          <li>Digite o código de 6 dígitos que o aplicativo mostrar:</li>
        </ol>
        <div className="max-w-xs space-y-2">
          <Label htmlFor="mfa-confirm">Código</Label>
          <Input
            id="mfa-confirm"
            name="code"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={6}
            required
          />
        </div>
        <Message state={done} />
        <Submit pending={confirming}>Confirmar e ligar</Submit>
      </form>
    );
  }

  return (
    <form action={startAction} className="max-w-sm space-y-3">
      <div className="space-y-2">
        <Label htmlFor="mfa-start-pw">Senha atual</Label>
        <Input id="mfa-start-pw" name="password" type="password" autoComplete="current-password" required />
      </div>
      <Message state={start} />
      <Submit pending={starting}>Começar a ativação</Submit>
    </form>
  );
}

export function ManageMfa() {
  const [codes, codesAction, generating] = useActionState<MfaState, FormData>(newRecoveryCodesAction, {});
  const [off, offAction, disabling] = useActionState<MfaState, FormData>(disableMfaAction, {});
  return (
    <div className="space-y-6">
      <details>
        <summary className="text-brand-institutional cursor-pointer text-sm font-medium">
          Gerar novos códigos de recuperação
        </summary>
        <form action={codesAction} className="mt-3 space-y-3">
          <PasswordAndCode prefix="rc" />
          <Message state={codes} />
          {codes.codes && <RecoveryCodes codes={codes.codes} />}
          <Submit pending={generating}>Gerar novos códigos</Submit>
        </form>
      </details>
      <details>
        <summary className="text-destructive cursor-pointer text-sm font-medium">
          Desligar a verificação em duas etapas
        </summary>
        <form action={offAction} className="mt-3 space-y-3">
          <p className="text-muted-foreground text-xs">
            Sem ela, quem descobrir sua senha entra na sua conta — inclusive no financeiro.
          </p>
          <PasswordAndCode prefix="off" />
          <Message state={off} />
          <Submit pending={disabling}>Desligar</Submit>
        </form>
      </details>
    </div>
  );
}
