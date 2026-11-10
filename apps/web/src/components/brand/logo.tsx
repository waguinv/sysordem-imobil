import Image from "next/image";
import { cn } from "@/lib/utils";

type Props = {
  variant?: "color" | "reverse" | "white";
  className?: string;
  priority?: boolean;
};

/** Logo horizontal SysOrdem Imobil (proporção 1000×230). `color` em fundo claro; `reverse` em fundo escuro. */
export function Logo({ variant = "color", className, priority }: Props) {
  return (
    <Image
      src={`/brand/logo-horizontal-${variant}.svg`}
      alt="SysOrdem Imobil"
      width={1000}
      height={230}
      unoptimized
      priority={priority}
      className={cn("h-8 w-auto select-none", className)}
    />
  );
}

export function BrandSymbol({ variant = "color", className }: Props) {
  return (
    <Image
      src={`/brand/simbolo-${variant}.svg`}
      alt="SysOrdem Imobil"
      width={510}
      height={470}
      unoptimized
      className={cn("h-8 w-auto select-none", className)}
    />
  );
}
