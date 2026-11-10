"use client";

import { ChevronDown, Headset } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { Logo } from "@/components/brand/logo";
import { isGroup, navFor, type NavGroup, type NavLink } from "@/lib/nav";
import { cn } from "@/lib/utils";

function isActive(pathname: string, href: string) {
  return href === "/app" ? pathname === "/app" : pathname === href || pathname.startsWith(`${href}/`);
}

function LinkItem({
  item,
  pathname,
  nested,
  onNavigate,
}: {
  item: NavLink;
  pathname: string;
  nested?: boolean;
  onNavigate?: () => void;
}) {
  const active = isActive(pathname, item.href);
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      className={cn(
        "group flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
        nested && "py-1.5 pl-10 font-normal",
        active
          ? "bg-sidebar-accent text-sidebar-accent-foreground font-semibold"
          : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
      )}
    >
      {Icon && !nested && (
        <Icon className={cn("size-[18px] shrink-0", active ? "text-brand" : "text-brand-muted")} />
      )}
      {nested && (
        <span
          className={cn("-ml-4 size-1.5 shrink-0 rounded-full", active ? "bg-brand" : "bg-brand-muted/40")}
        />
      )}
      <span className="flex-1 truncate">{item.title}</span>
      {item.soon && <span className="text-brand-muted/70 text-[10px] font-normal">{item.soon}</span>}
    </Link>
  );
}

function GroupItem({
  group,
  pathname,
  onNavigate,
}: {
  group: NavGroup;
  pathname: string;
  onNavigate?: () => void;
}) {
  const containsActive = group.items.some((i) => isActive(pathname, i.href));
  const [open, setOpen] = useState(containsActive);
  const Icon = group.icon;
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className={cn(
          "text-sidebar-foreground/80 hover:bg-sidebar-accent/60 flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
          containsActive && "text-sidebar-accent-foreground",
        )}
      >
        <Icon className={cn("size-[18px] shrink-0", containsActive ? "text-brand" : "text-brand-muted")} />
        <span className="flex-1 truncate text-left">{group.title}</span>
        <ChevronDown className={cn("text-brand-muted size-4 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="mt-0.5 space-y-0.5 pb-1">
          {group.items.map((i) => (
            <LinkItem key={i.href} item={i} pathname={pathname} nested onNavigate={onNavigate} />
          ))}
        </div>
      )}
    </div>
  );
}

export function SidebarContent({
  permissions,
  onNavigate,
}: {
  permissions: string[];
  onNavigate?: () => void;
}) {
  const pathname = usePathname();
  const entries = navFor(permissions);
  return (
    <div className="flex h-full flex-col">
      <div className="border-sidebar-border flex h-16 shrink-0 items-center border-b px-5">
        <Link href="/app" onClick={onNavigate} aria-label="Tela inicial">
          <Logo className="h-8" priority />
        </Link>
      </div>
      <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 py-4" aria-label="Menu principal">
        {entries.map((e) =>
          isGroup(e) ? (
            <GroupItem key={e.title} group={e} pathname={pathname} onNavigate={onNavigate} />
          ) : (
            <LinkItem key={e.href} item={e} pathname={pathname} onNavigate={onNavigate} />
          ),
        )}
      </nav>
      <div className="border-sidebar-border border-t p-3">
        <a
          href="mailto:suporte@sysordem.example"
          className="bg-brand text-brand-deep hover:bg-brand/90 flex items-center justify-center gap-2 rounded-lg px-3 py-2.5 text-sm font-semibold shadow-sm transition-colors"
        >
          <Headset className="size-4" /> Suporte
        </a>
      </div>
    </div>
  );
}

export function Sidebar({ permissions }: { permissions: string[] }) {
  return (
    <aside className="bg-sidebar border-sidebar-border fixed inset-y-0 left-0 z-30 hidden w-64 border-r lg:block">
      <SidebarContent permissions={permissions} />
    </aside>
  );
}
