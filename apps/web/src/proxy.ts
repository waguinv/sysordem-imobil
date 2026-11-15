import { NextResponse, type NextRequest } from "next/server";
import { buildCsp, newNonce } from "@/lib/csp";

const SESSION_COOKIE = "imobil_session";

/**
 * 1) CSP com nonce por requisição em todas as páginas (Next aplica o nonce aos próprios scripts).
 * 2) Barreira de UX: sem cookie de sessão, /app/* vai para o login.
 *    A autorização real acontece na API (token + vínculo ativo revalidados a cada chamada).
 */
export function proxy(req: NextRequest) {
  const path = req.nextUrl.pathname;
  if ((path === "/app" || path.startsWith("/app/")) && !req.cookies.get(SESSION_COOKIE)?.value) {
    return NextResponse.redirect(new URL("/entrar", req.nextUrl));
  }
  const nonce = newNonce();
  const csp = buildCsp(nonce, process.env.NODE_ENV === "development");
  const requestHeaders = new Headers(req.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);
  const res = NextResponse.next({ request: { headers: requestHeaders } });
  res.headers.set("Content-Security-Policy", csp);
  return res;
}

export const config = {
  // Páginas e rotas; fora: arquivos estáticos do Next, ícones e prefetch (sem HTML a proteger).
  matcher: [
    {
      source: "/((?!_next/static|_next/image|favicon.ico|icon.svg|brand/|healthz).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
