"use client";

import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/primitives";

/** Copia um texto (Pix copia e cola, linha digitável) para a área de transferência. */
export function CopyButton({ text, label = "Copiar" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 2000);
        } catch {
          /* sem permissão de área de transferência: o texto continua selecionável */
        }
      }}
    >
      {done ? <Check /> : <Copy />} {done ? "Copiado" : label}
    </Button>
  );
}
