import type { Metadata } from "next";
import type { PlanInfo } from "@/lib/billing";
import { API_URL } from "@/lib/session";
import { SignupForm } from "./signup-form";

export const metadata: Metadata = { title: "Criar conta" };

export default async function CadastroPage() {
  const res = await fetch(`${API_URL}/api/v1/public/plans`, { cache: "no-store" });
  const plans = res.ok ? ((await res.json()) as PlanInfo[]) : [];
  return (
    <div className="space-y-6">
      <div className="space-y-1.5">
        <h1 className="text-brand-institutional text-2xl font-bold">Criar conta da empresa</h1>
        <p className="text-muted-foreground text-sm">
          Teste grátis por 14 dias. Depois, a assinatura é mensal por Pix ou boleto e pode ser cancelada a
          qualquer momento.
        </p>
      </div>
      <SignupForm plans={plans} />
    </div>
  );
}
