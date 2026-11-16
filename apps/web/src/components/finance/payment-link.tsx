import { ExternalLink } from "lucide-react";

/** Link para a página do PSP (QR Code Pix / boleto em PDF), só se for https. */
export function PaymentLink({
  url,
  label = "Abrir boleto / QR Code",
}: {
  url: string | null;
  label?: string;
}) {
  if (!url || !url.startsWith("https://")) return null;
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="text-brand-institutional inline-flex items-center gap-1 text-sm font-medium hover:underline"
    >
      <ExternalLink className="size-4" /> {label}
    </a>
  );
}
