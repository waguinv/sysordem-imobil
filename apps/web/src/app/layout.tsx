import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import { connection } from "next/server";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });

export const metadata: Metadata = {
  title: { default: "SysOrdem Imobil", template: "%s · SysOrdem Imobil" },
  description: "Gestão de loteamentos: lotes, vendas, contratos, parcelas e financeiro.",
  applicationName: "SysOrdem Imobil",
};

export const viewport: Viewport = { themeColor: "#003B2D" };

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  // CSP com nonce (src/proxy.ts) exige renderização dinâmica: páginas estáticas não recebem o nonce.
  await connection();
  return (
    <html lang="pt-BR" className={inter.variable}>
      <body className="min-h-dvh font-sans">{children}</body>
    </html>
  );
}
