/** Liveness do web (healthcheck do container/deploy). Não chama a API nem lê cookies. */
export const dynamic = "force-dynamic";

export function GET() {
  return Response.json({ status: "ok", service: "sysordem-imobil-web" });
}
