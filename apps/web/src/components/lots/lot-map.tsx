import Link from "next/link";
import { brl, LOT_STATUS, m2 } from "@/lib/format";
import type { DevelopmentMap } from "@/lib/types";
import { cn } from "@/lib/utils";

export function StatusLegend({ counts }: { counts?: Record<string, number> }) {
  return (
    <div className="text-muted-foreground flex flex-wrap gap-x-5 gap-y-2 text-xs">
      {Object.entries(LOT_STATUS).map(([key, s]) => (
        <span key={key} className="flex items-center gap-2">
          <i className={cn("size-2.5 rounded-full", s.dot)} />
          {s.label}
          {counts && <strong className="text-foreground">{counts[key] ?? 0}</strong>}
        </span>
      ))}
    </div>
  );
}

/**
 * Mapa esquemático responsivo: uma faixa por quadra, lotes em grade que se ajusta à largura.
 * (A planta georreferenciada usa o campo `geometry` quando o formato da planta for definido.)
 */
export function LotMap({ map }: { map: DevelopmentMap }) {
  if (map.blocks.length === 0) {
    return <p className="text-muted-foreground text-sm">Nenhuma quadra cadastrada ainda.</p>;
  }
  return (
    <div className="space-y-4">
      {map.blocks.map((block) => (
        <section
          key={block.id}
          aria-label={`Quadra ${block.code}`}
          className="bg-brand-surface rounded-xl p-3"
        >
          <h3 className="text-brand-institutional mb-2 text-sm font-semibold">
            Quadra {block.code}{" "}
            <span className="text-muted-foreground font-normal">· {block.lots.length} lotes</span>
          </h3>
          {block.lots.length === 0 ? (
            <p className="text-muted-foreground text-xs">Sem lotes nesta quadra.</p>
          ) : (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(64px,1fr))] gap-1.5">
              {block.lots.map((lot) => {
                const s = LOT_STATUS[lot.status] ?? LOT_STATUS.disponivel!;
                return (
                  <Link
                    key={lot.id}
                    href={`/app/lotes/${lot.id}`}
                    title={`Lote ${lot.number} · ${s.label} · ${m2(lot.area_m2)} · ${brl(lot.price)}`}
                    className={cn(
                      "focus-visible:ring-ring flex min-h-16 flex-col items-center justify-center rounded-md transition-transform outline-none hover:scale-[1.04] focus-visible:ring-[3px]",
                      s.cell,
                    )}
                  >
                    <small className="text-[10px] opacity-80">Q{block.code}</small>
                    <strong className="text-base leading-tight">{lot.number}</strong>
                    <span className="sr-only">{s.label}</span>
                  </Link>
                );
              })}
            </div>
          )}
        </section>
      ))}
    </div>
  );
}
