import { describe, expect, it } from "vitest";
import {
  discountLabel,
  installmentLabel,
  installmentStatus,
  payableStatus,
  maskDoc,
  parseDecimalBR,
  timeLeft,
} from "@/lib/format";
import { decimalBR, toCSV } from "@/lib/csv";
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

  it("leads e propostas seguem as próprias permissões", () => {
    expect(flat(["leads:view"])).toContain("/app/leads");
    expect(flat(["leads:view"])).not.toContain("/app/propostas");
    expect(flat(["proposals:view"])).toContain("/app/propostas");
    expect(flat(["tenant:view"])).not.toContain("/app/leads");
  });

  it("contas a receber exige finance:view", () => {
    expect(flat(["finance:view"])).toContain("/app/financeiro/receber");
    expect(flat(["contracts:view"])).not.toContain("/app/financeiro/receber");
  });

  it("contas a pagar e comissões exigem finance:view; regras exigem settings:manage", () => {
    const fin = flat(["finance:view"]);
    expect(fin).toContain("/app/financeiro/pagar");
    expect(fin).toContain("/app/comissoes");
    expect(fin).not.toContain("/app/configuracoes/financeiro");
    expect(flat(["settings:manage"])).toContain("/app/configuracoes/financeiro");
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
  it("aceita mais casas quando pedido (porcentagens)", () => {
    expect(parseDecimalBR("0,8125", 4)).toBe("0.8125");
    expect(parseDecimalBR("1,5%", 4)).toBe("1.5");
    expect(parseDecimalBR("0,81255", 4)).toBeNull();
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

describe("discountLabel", () => {
  it("descreve desconto, ágio e preço de tabela", () => {
    expect(discountLabel("5.00")).toBe("5% de desconto");
    expect(discountLabel("-2.50")).toBe("2,5% acima da tabela");
    expect(discountLabel("0.00")).toBe("preço de tabela");
  });
});

describe("parcelas", () => {
  it("rotula a situação considerando atraso e pagamento parcial", () => {
    expect(installmentStatus({ status: "paga", overdue: false, paid_principal: "10" }).label).toBe("Paga");
    expect(installmentStatus({ status: "aberta", overdue: true, paid_principal: "0" })).toEqual({
      label: "Vencida",
      badge: "danger",
    });
    expect(installmentStatus({ status: "aberta", overdue: true, paid_principal: "5" }).label).toBe(
      "Vencida (parcial)",
    );
    expect(installmentStatus({ status: "aberta", overdue: false, paid_principal: "5" }).label).toBe(
      "Parcialmente paga",
    );
    expect(installmentStatus({ status: "aberta", overdue: false, paid_principal: "0" }).label).toBe(
      "A vencer",
    );
  });
  it("numera parcelas e entrada", () => {
    expect(installmentLabel(0, 12)).toBe("Entrada");
    expect(installmentLabel(3, 12)).toBe("3/12");
    expect(installmentLabel(3)).toBe("Parcela 3");
  });
});

describe("contas a pagar", () => {
  it("rotula a situação", () => {
    expect(payableStatus({ status: "aberta", overdue: true })).toEqual({ label: "Vencida", badge: "danger" });
    expect(payableStatus({ status: "aberta", overdue: false }).label).toBe("A pagar");
    expect(payableStatus({ status: "paga", overdue: false }).label).toBe("Paga");
    expect(payableStatus({ status: "cancelada", overdue: false }).label).toBe("Cancelada");
  });
});

describe("csv", () => {
  it("usa ponto e vírgula, vírgula decimal, BOM e escapa campos", () => {
    const csv = toCSV(
      ["Nome", "Valor"],
      [
        ["Ana; Bia", decimalBR("1234.50")],
        ['Diz "oi"', null],
      ],
    );
    expect(csv.startsWith("﻿")).toBe(true);
    expect(csv).toBe('﻿Nome;Valor\r\n"Ana; Bia";1234,50\r\n"Diz ""oi""";\r\n');
  });
  it("relatórios aparecem para quem vê contratos", () => {
    expect(flat(["contracts:view"])).toContain("/app/relatorios");
    expect(flat(["tenant:view"])).not.toContain("/app/relatorios");
  });
});

describe("parcela renegociada", () => {
  it("tem rótulo próprio", () => {
    expect(installmentStatus({ status: "renegociada", overdue: false, paid_principal: "0" }).label).toBe(
      "Renegociada",
    );
  });
});
