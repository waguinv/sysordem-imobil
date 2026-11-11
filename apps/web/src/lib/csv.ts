/* CSV para Excel em português: separador ";", vírgula decimal e BOM UTF-8 (acentos corretos). */

export type Cell = string | number | null | undefined;

/** Valor monetário da API ("1234.50") → "1234,50" (sem separador de milhar, para o Excel somar). */
export function decimalBR(v: string | null | undefined): string {
  return v == null ? "" : v.replace(".", ",");
}

function escape(cell: Cell): string {
  const s = cell == null ? "" : String(cell);
  return /[";\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCSV(headers: string[], rows: Cell[][]): string {
  const lines = [headers, ...rows].map((r) => r.map(escape).join(";"));
  return "﻿" + lines.join("\r\n") + "\r\n";
}
