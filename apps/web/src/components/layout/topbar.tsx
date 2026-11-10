"use client";

import { Building2, LogOut, Menu, X } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/primitives";
import { logoutAction } from "@/lib/actions";
import { initials } from "@/lib/utils";
import { SidebarContent } from "./sidebar";

type Props = {
  userName: string;
  userEmail: string;
  roleLabel: string;
  tenantName: string;
  permissions: string[];
};

export function Topbar({ userName, userEmail, roleLabel, tenantName, permissions }: Props) {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <>
      <header className="bg-brand-institutional sticky top-0 z-20 flex h-16 items-center gap-3 px-4 text-white shadow-sm sm:px-6">
        <Button
          variant="ghost"
          size="icon"
          className="text-white hover:bg-white/10 hover:text-white lg:hidden"
          onClick={() => setMobileOpen(true)}
          aria-label="Abrir menu"
        >
          <Menu className="size-5" />
        </Button>
        <div className="flex min-w-0 items-center gap-2 text-sm">
          <Building2 className="text-brand size-4 shrink-0" />
          <span className="truncate font-medium">{tenantName}</span>
        </div>
        <details className="relative ml-auto">
          <summary className="flex cursor-pointer list-none items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-white/10 [&::-webkit-details-marker]:hidden">
            <span className="bg-brand text-brand-deep grid size-8 place-items-center rounded-full text-xs font-bold">
              {initials(userName)}
            </span>
            <span className="hidden text-left leading-tight sm:block">
              <span className="block text-sm font-medium">{userName}</span>
              <span className="block text-xs text-white/60">{roleLabel}</span>
            </span>
          </summary>
          <div className="text-foreground absolute right-0 mt-2 w-64 rounded-xl border bg-white p-2 shadow-lg">
            <div className="border-b px-2 pt-1 pb-2">
              <p className="truncate text-sm font-medium">{userName}</p>
              <p className="text-muted-foreground truncate text-xs">{userEmail}</p>
            </div>
            <form action={logoutAction}>
              <button
                type="submit"
                className="hover:bg-accent mt-1 flex w-full items-center gap-2 rounded-md px-2 py-2 text-sm"
              >
                <LogOut className="text-brand-muted size-4" /> Sair
              </button>
            </form>
          </div>
        </details>
      </header>

      {mobileOpen && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Menu">
          <button
            type="button"
            className="absolute inset-0 bg-black/40"
            aria-label="Fechar menu"
            onClick={() => setMobileOpen(false)}
          />
          <div className="bg-sidebar animate-in slide-in-from-left absolute inset-y-0 left-0 w-72 shadow-xl">
            <button
              type="button"
              onClick={() => setMobileOpen(false)}
              className="text-brand-muted absolute top-4 right-3 rounded-md p-1 hover:bg-black/5"
              aria-label="Fechar menu"
            >
              <X className="size-5" />
            </button>
            <SidebarContent permissions={permissions} onNavigate={() => setMobileOpen(false)} />
          </div>
        </div>
      )}
    </>
  );
}
