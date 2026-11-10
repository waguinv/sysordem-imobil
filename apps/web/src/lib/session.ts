import "server-only";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

/** Cookie httpOnly com o token de acesso emitido pela API (o navegador nunca lê o token). */
export const SESSION_COOKIE = "imobil_session";

export const API_URL = process.env.API_INTERNAL_URL ?? "http://localhost:8000";

export type Me = {
  user_id: string;
  email: string;
  full_name: string;
  role: string;
  role_label: string;
  permissions: string[];
  tenant: { id: string; slug: string; name: string };
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function getToken(): Promise<string | undefined> {
  return (await cookies()).get(SESSION_COOKIE)?.value;
}

/** Chamada server-side à API com o token da sessão. 401 → volta ao login. */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await getToken();
  if (!token) redirect("/entrar");
  const res = await fetch(`${API_URL}/api/v1${path}`, {
    ...init,
    cache: "no-store",
    headers: { ...init.headers, Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
  });
  if (res.status === 401) redirect("/entrar?expirada=1");
  if (!res.ok) throw new ApiError(res.status, `API ${res.status} em ${path}`);
  return (await res.json()) as T;
}

export function getMe(): Promise<Me> {
  return api<Me>("/me");
}
