"use server";

import { revalidatePath } from "next/cache";
import type { FormState } from "@/lib/actions-fase1";
import { api, API_URL, ApiError } from "@/lib/session";

const str = (f: FormData, k: string) => String(f.get(k) ?? "").trim();
const flag = (f: FormData, k: string) => f.get(k) === "on";

async function guard(fn: () => Promise<FormState>): Promise<FormState> {
  try {
    return await fn();
  } catch (e) {
    if (e instanceof ApiError) return { error: e.message };
    throw e;
  }
}

function refresh(slug?: string) {
  revalidatePath("/app/site");
  if (slug) revalidatePath(`/s/${slug}`, "layout");
}

export async function saveSiteAction(slug: string, _p: FormState, f: FormData): Promise<FormState> {
  return guard(async () => {
    await api("/site/settings", {
      method: "PUT",
      body: JSON.stringify({
        published: flag(f, "published"),
        title: str(f, "title") || null,
        tagline: str(f, "tagline") || null,
        about: str(f, "about") || null,
        contact_phone: str(f, "contact_phone") || null,
        contact_whatsapp: str(f, "contact_whatsapp") || null,
        contact_email: str(f, "contact_email") || null,
        show_prices: flag(f, "show_prices"),
        lead_form_enabled: flag(f, "lead_form_enabled"),
      }),
    });
    refresh(slug);
    return { ok: flag(f, "published") ? "Site salvo e no ar" : "Site salvo (fora do ar)" };
  });
}

export async function publishDevelopmentAction(
  slug: string,
  developmentId: string,
  _p: FormState,
  f: FormData,
): Promise<FormState> {
  const published = flag(f, "published");
  const description = str(f, "description");
  if (published && !description) return { error: "Escreva o texto público antes de publicar." };
  return guard(async () => {
    await api(`/developments/${developmentId}/site`, {
      method: "PATCH",
      body: JSON.stringify({ published, description: description || null }),
    });
    refresh(slug);
    return { ok: published ? "Publicado no site" : "Fora do site" };
  });
}

/** Formulário público de contato (sem sessão). A API valida, limita e grava o consentimento. */
export async function submitLeadAction(slug: string, _p: FormState, f: FormData): Promise<FormState> {
  if (!flag(f, "consent")) return { error: "Para entrarmos em contato, marque a autorização." };
  const body = {
    name: str(f, "name"),
    email: str(f, "email") || null,
    phone: str(f, "phone") || null,
    message: str(f, "message") || null,
    development_id: str(f, "development_id") || null,
    lot_id: str(f, "lot_id") || null,
    consent: true,
    website: str(f, "website") || null,
  };
  if (body.name.length < 2) return { error: "Informe seu nome." };
  if (!body.email && !body.phone) return { error: "Informe telefone ou e-mail." };
  const res = await fetch(`${API_URL}/api/v1/public/sites/${encodeURIComponent(slug)}/leads`, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.status === 202) return { ok: "Recebemos seu contato. Em breve falaremos com você!" };
  if (res.status === 429) return { error: "Muitos envios. Tente de novo mais tarde ou fale por telefone." };
  if (res.status === 422) {
    const detail = ((await res.json().catch(() => ({}))) as { detail?: unknown }).detail;
    return {
      error: typeof detail === "string" ? detail : "Confira os dados: telefone com DDD ou e-mail válido.",
    };
  }
  return { error: "Não foi possível enviar agora. Tente de novo em instantes." };
}
