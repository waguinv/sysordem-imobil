import { AlertTriangle, Clock } from "lucide-react";
import Link from "next/link";
import { type BillingStatus, daysUntil } from "@/lib/billing";
import { dateBR } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Aviso no topo do sistema sobre a assinatura (teste acabando, atraso, suspensão). */
export function SubscriptionBanner({ st, canManage }: { st: BillingStatus; canManage: boolean }) {
  let tone: "info" | "warn" | "danger" | null = null;
  let text = "";
  if (st.state === "teste" && st.covered_until) {
    const d = daysUntil(st.covered_until);
    if (d <= 7) {
      tone = "info";
      text =
        d <= 0
          ? "O teste grátis termina hoje."
          : `O teste grátis termina em ${d} dia(s) (${dateBR(st.covered_until)}).`;
    }
  } else if (st.state === "em_atraso" && st.grace_until) {
    tone = "warn";
    text = `Pagamento da assinatura em atraso. Regularize até ${dateBR(st.grace_until)} para não suspender as alterações.`;
  } else if (st.state === "suspensa" || st.state === "cancelada") {
    tone = "danger";
    text =
      st.state === "suspensa"
        ? "Assinatura suspensa: o sistema está só para consulta até o pagamento."
        : "Assinatura cancelada: o sistema está só para consulta.";
  }
  if (!tone) return null;
  return (
    <div
      role="status"
      className={cn(
        "flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-sm sm:px-6",
        tone === "info" && "bg-brand/10 text-brand-institutional",
        tone === "warn" && "bg-amber-100 text-amber-900",
        tone === "danger" && "bg-red-100 text-red-900",
      )}
    >
      <span className="flex items-center gap-2">
        {tone === "info" ? <Clock className="size-4" /> : <AlertTriangle className="size-4" />} {text}
      </span>
      {canManage ? (
        <Link href="/app/configuracoes/assinatura" className="font-medium underline">
          {tone === "info" ? "Ver planos" : "Regularizar"}
        </Link>
      ) : (
        <span className="text-xs opacity-80">Fale com o proprietário da conta.</span>
      )}
    </div>
  );
}
