import { Construction } from "lucide-react";
import { notFound } from "next/navigation";
import { Card, CardContent } from "@/components/ui/primitives";
import { findRoute } from "@/lib/nav";

/** Placeholder "Em breve" para telas do menu que pertencem a fases futuras. */
export default async function SoonPage({ params }: { params: Promise<{ slug: string[] }> }) {
  const { slug } = await params;
  const route = findRoute(`/app/${slug.join("/")}`);
  if (!route?.soon) notFound();
  return (
    <div className="space-y-6">
      <h1 className="text-brand-institutional text-2xl font-bold">{route.title}</h1>
      <Card>
        <CardContent className="flex flex-col items-center gap-3 py-10 text-center">
          <span className="bg-brand/10 text-brand-institutional grid size-12 place-items-center rounded-full">
            <Construction className="size-6" />
          </span>
          <p className="font-medium">Em construção</p>
          <p className="text-muted-foreground max-w-sm text-sm">
            Esta tela faz parte da {route.soon} do roadmap e será entregue com testes e revisão de segurança.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
