import { describe, expect, it } from "vitest";
import { maskDoc, parseDecimalBR, timeLeft } from "@/lib/format";
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

  it("proprietários exigem owners:view", () => {
    expect(flat(["developments:view"])).toContain("/app/empreendimentos");
    expect(flat(["developments:view"])).not.toContain("/app/proprietarios");
    expect(flat(["owners:view"])).toContain("/app/proprietarios");
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

describe("parseDecimalBR", () => {
  it("normaliza formatos brasileiros sem usar float", () => {
    expect(parseDecimalBR("150.000,50")).toBe("150000.50");
    expect(parseDecimalBR("R$ 1.234,5")).toBe("1234.5");
    expect(parseDecimalBR("300")).toBe("300");
    expect(parseDecimalBR("300.25")).toBe("300.25");
  });
  it("rejeita entradas inválidas", () => {
    expect(parseDecimalBR("")).toBeNull();
    expect(parseDecimalBR("abc")).toBeNull();
    expect(parseDecimalBR("10,005")).toBeNull();
    expect(parseDecimalBR("-5")).toBeNull();
  });
});

describe("timeLeft e maskDoc", () => {
  const now = new Date("2026-11-10T12:00:00Z");
  it("descreve o prazo restante", () => {
    expect(timeLeft("2026-11-13T12:00:00Z", now)).toBe("em 3 dias");
    expect(timeLeft("2026-11-13T11:59:00Z", now)).toBe("em 3 dias");
    expect(timeLeft("2026-11-10T17:30:00Z", now)).toBe("em 5 h");
    expect(timeLeft("2026-11-10T12:20:00Z", now)).toBe("em 20 min");
    expect(timeLeft("2026-11-10T11:00:00Z", now)).toBe("vencida");
  });
  it("mascara documento", () => {
    expect(maskDoc("52998224725")).toBe("CPF •••725");
    expect(maskDoc("11222333000181")).toBe("CNPJ •••181");
    expect(maskDoc(null)).toBe("—");
  });
});
