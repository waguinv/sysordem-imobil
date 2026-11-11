import {
  Building2,
  FileSignature,
  FileBarChart,
  Globe,
  HandCoins,
  Home,
  LandPlot,
  Settings,
  Users,
  UsersRound,
  Wallet,
  type LucideIcon,
} from "lucide-react";

export type NavLink = {
  title: string;
  href: string;
  icon?: LucideIcon;
  /** permissão exigida (vinda de /api/v1/me); a API valida de novo no servidor */
  permission: string;
  /** fase do roadmap em que a tela será entregue */
  soon?: string;
  /** outras rotas que também destacam este item (ex.: telas de detalhe) */
  alsoActive?: string[];
};

export type NavGroup = { title: string; icon: LucideIcon; items: NavLink[] };
export type NavEntry = NavLink | NavGroup;

export const isGroup = (e: NavEntry): e is NavGroup => "items" in e;

export const NAV: NavEntry[] = [
  { title: "Tela inicial", href: "/app", icon: Home, permission: "tenant:view" },
  {
    title: "Empreendimentos",
    icon: LandPlot,
    items: [
      {
        title: "Empreendimentos e lotes",
        href: "/app/empreendimentos",
        permission: "developments:view",
        alsoActive: ["/app/lotes"],
      },
      { title: "Proprietários da área", href: "/app/proprietarios", permission: "owners:view" },
    ],
  },
  {
    title: "Comercial",
    icon: UsersRound,
    items: [
      { title: "Leads", href: "/app/leads", permission: "leads:view" },
      { title: "Propostas", href: "/app/propostas", permission: "proposals:view" },
      { title: "Reservas", href: "/app/reservas", permission: "reservations:view" },
      { title: "Compradores", href: "/app/compradores", permission: "customers:view" },
      { title: "Corretores", href: "/app/corretores", permission: "brokers:view" },
    ],
  },
  { title: "Contratos", href: "/app/contratos", icon: FileSignature, permission: "contracts:view" },
  {
    title: "Financeiro",
    icon: Wallet,
    items: [
      {
        title: "Contas a receber",
        href: "/app/financeiro/receber",
        permission: "finance:view",
        alsoActive: ["/app/financeiro/parcelas"],
      },
      { title: "Contas a pagar", href: "/app/financeiro/pagar", permission: "finance:view" },
      {
        title: "Conciliação",
        href: "/app/financeiro/conciliacao",
        permission: "tenant:view",
        soon: "Fase 4",
      },
    ],
  },
  { title: "Comissões e repasses", href: "/app/comissoes", icon: HandCoins, permission: "finance:view" },
  {
    title: "Relatórios",
    href: "/app/relatorios",
    icon: FileBarChart,
    permission: "tenant:view",
    soon: "Fase 3c",
  },
  { title: "Site público", href: "/app/site", icon: Globe, permission: "tenant:view", soon: "Fase 5" },
  {
    title: "Configurações",
    icon: Settings,
    items: [
      { title: "Equipe e acessos", href: "/app/equipe", icon: Users, permission: "members:view" },
      {
        title: "Política de contratos",
        href: "/app/configuracoes/politica-contratos",
        permission: "settings:manage",
      },
      {
        title: "Regras de comissão",
        href: "/app/configuracoes/financeiro",
        permission: "settings:manage",
      },
      { title: "Empresa", href: "/app/empresa", icon: Building2, permission: "tenant:view", soon: "Fase 5" },
    ],
  },
];

/** Menu filtrado pelas permissões do usuário (grupos vazios somem). */
export function navFor(permissions: readonly string[]): NavEntry[] {
  const allowed = (i: NavLink) => permissions.includes(i.permission);
  return NAV.flatMap<NavEntry>((e) => {
    if (!isGroup(e)) return allowed(e) ? [e] : [];
    const items = e.items.filter(allowed);
    return items.length ? [{ ...e, items }] : [];
  });
}

export function findRoute(href: string): NavLink | undefined {
  for (const e of NAV) {
    if (isGroup(e)) {
      const hit = e.items.find((i) => i.href === href);
      if (hit) return hit;
    } else if (e.href === href) return e;
  }
  return undefined;
}
