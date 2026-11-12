import { CheckCircle2, Circle } from "lucide-react";
import Link from "next/link";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/primitives";
import type { Onboarding } from "@/lib/billing";

/** Primeiros passos da empresa (some quando tudo estiver feito). */
export function OnboardingCard({ o }: { o: Onboarding }) {
  if (o.done >= o.total) return null;
  const pct = Math.round((o.done / o.total) * 100);
  return (
    <Card className="border-brand/40">
      <CardHeader>
        <div>
          <CardTitle>Primeiros passos</CardTitle>
          <CardDescription>
            {o.done} de {o.total} concluídos. Siga a ordem para colocar o loteamento para vender.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="bg-muted h-2 overflow-hidden rounded-full" aria-hidden="true">
          <div className="bg-brand h-full rounded-full" style={{ width: `${pct}%` }} />
        </div>
        <ul className="grid gap-1 sm:grid-cols-2">
          {o.steps.map((s) => (
            <li key={s.key}>
              <Link
                href={s.href}
                className="hover:bg-accent/60 flex items-center gap-2 rounded-md px-2 py-1.5 text-sm"
              >
                {s.done ? (
                  <CheckCircle2 className="text-brand size-4 shrink-0" />
                ) : (
                  <Circle className="text-muted-foreground size-4 shrink-0" />
                )}
                <span className={s.done ? "text-muted-foreground line-through" : undefined}>{s.title}</span>
              </Link>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
