import "server-only";
import { createHmac } from "node:crypto";
import { headers } from "next/headers";

/**
 * Cabeçalhos com o IP do visitante, assinados para a API (limites por IP no login, senha, cadastro e contato).
 *
 * O IP vem do `X-Real-IP`, que o nginx grava com o endereço real da conexão (sobrescreve o que o visitante
 * mandar) — o web só recebe conexões do nginx. Sem CLIENT_IP_SECRET ou sem o cabeçalho (ex.: desenvolvimento
 * sem nginx), não manda nada e a API não aplica limite por IP.
 */
export async function clientIpHeaders(): Promise<Record<string, string>> {
  const secret = process.env.CLIENT_IP_SECRET;
  if (!secret) return {};
  const ip = (await headers()).get("x-real-ip")?.trim();
  if (!ip || ip.length > 64) return {};
  return {
    "X-Client-IP": ip,
    "X-Client-IP-Sig": createHmac("sha256", secret).update(ip).digest("hex"),
  };
}
