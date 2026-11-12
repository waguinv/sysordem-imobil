import { Card, CardContent } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";

/** Cartão de resumo (valor em destaque + legenda) das telas financeiras. */
export function Stat({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint: string;
  tone?: "danger";
}) {
  return (
    <Card>
      <CardContent className="space-y-1">
        <p className="text-muted-foreground text-xs">{label}</p>
        <p
          className={cn(
            "text-xl font-bold tabular-nums",
            tone === "danger" ? "text-red-700" : "text-brand-institutional",
          )}
        >
          {value}
        </p>
        <p className="text-muted-foreground text-xs">{hint}</p>
      </CardContent>
    </Card>
  );
}
