"use client";

import { Printer } from "lucide-react";
import { Button } from "@/components/ui/primitives";

/** Imprime a página (ou salva em PDF pelo diálogo de impressão do navegador). */
export function PrintButton({ label = "Imprimir / salvar PDF" }: { label?: string }) {
  return (
    <Button type="button" variant="outline" onClick={() => window.print()}>
      <Printer /> {label}
    </Button>
  );
}
