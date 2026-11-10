import { NextResponse, type NextRequest } from "next/server";

const SESSION_COOKIE = "imobil_session";

/**
 * Barreira de UX: sem cookie de sessão, /app/* vai para o login.
 * A autorização real acontece na API (token + vínculo ativo revalidados a cada chamada).
 */
export function proxy(req: NextRequest) {
  if (!req.cookies.get(SESSION_COOKIE)?.value) {
    return NextResponse.redirect(new URL("/entrar", req.nextUrl));
  }
  return NextResponse.next();
}

export const config = { matcher: ["/app/:path*"] };
