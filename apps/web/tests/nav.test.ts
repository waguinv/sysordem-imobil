import { describe, expect, it } from "vitest";
import { findRoute, isGroup, NAV, navFor } from "@/lib/nav";
import { initials } from "@/lib/utils";

const flat = (perms: string[]) =>
  navFor(perms).flatMap((e) => (isGroup(e) ? e.items.map((i) => i.href) : [e.href]));

describe("menu por permissão", () => {
  it("sem permissões não mostra nada", () => {
    expect(navFor([])).toEqual([]);
  });

  it("perfil leitura não vê Equipe e acessos", () => {
    const hrefs = flat(["tenant:view"]);
    expect(hrefs).toContain("/app");
    expect(hrefs).not.toContain("/app/equipe");
  });

  it("perfil com members:view vê Equipe e acessos", () => {
    expect(flat(["tenant:view", "members:view"])).toContain("/app/equipe");
  });

  it("grupos sem itens permitidos somem", () => {
    const groups = navFor(["members:view"]).filter(isGroup);
    expect(groups.map((g) => g.title)).toEqual(["Configurações"]);
  });

  it("rotas são únicas e findRoute encontra todas", () => {
    const all = NAV.flatMap((e) => (isGroup(e) ? e.items : [e]));
    const hrefs = all.map((i) => i.href);
    expect(new Set(hrefs).size).toBe(hrefs.length);
    for (const h of hrefs) expect(findRoute(h)?.href).toBe(h);
  });
});

describe("initials", () => {
  it("usa primeira e última palavra", () => {
    expect(initials("Maria da Silva")).toBe("MS");
    expect(initials("ana")).toBe("A");
    expect(initials("  ")).toBe("?");
  });
});
