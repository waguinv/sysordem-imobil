import { Sidebar } from "@/components/layout/sidebar";
import { Topbar } from "@/components/layout/topbar";
import { getMe } from "@/lib/session";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  // A API revalida token + vínculo ativo a cada chamada; sem sessão válida, getMe() redireciona ao login.
  const me = await getMe();
  return (
    <div className="bg-brand-surface/50 min-h-dvh">
      <Sidebar permissions={me.permissions} />
      <div className="lg:pl-64">
        <Topbar
          userName={me.full_name}
          userEmail={me.email}
          roleLabel={me.role_label}
          tenantName={me.tenant.name}
          permissions={me.permissions}
        />
        <main className="mx-auto w-full max-w-7xl px-4 py-6 sm:px-6 lg:py-8">{children}</main>
      </div>
    </div>
  );
}
