import { NextResponse, type NextRequest } from "next/server";
import { API_URL, getToken } from "@/lib/session";

/** Prévia do PDF do quadro-resumo (o mesmo enviado para assinatura). A API valida a permissão. */
export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const token = await getToken();
  if (!token) return NextResponse.redirect(new URL("/entrar", _req.url));
  const res = await fetch(`${API_URL}/api/v1/contracts/${encodeURIComponent(id)}/document.pdf`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (!res.ok) return NextResponse.json({ error: "Documento indisponível" }, { status: res.status });
  return new NextResponse(await res.arrayBuffer(), {
    headers: {
      "Content-Type": "application/pdf",
      "Content-Disposition": res.headers.get("content-disposition") ?? "inline",
      "Cache-Control": "no-store",
    },
  });
}
