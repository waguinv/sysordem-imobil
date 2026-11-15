import { describe, expect, it } from "vitest";
import { buildCsp, newNonce } from "../src/lib/csp";

describe("CSP", () => {
  it("usa nonce e bloqueia o que não é próprio", () => {
    const csp = buildCsp("abc123", false);
    expect(csp).toContain("script-src 'self' 'nonce-abc123' 'strict-dynamic'");
    expect(csp).not.toContain("unsafe-eval");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("upgrade-insecure-requests");
  });

  it("libera eval e websocket só em desenvolvimento (HMR)", () => {
    const csp = buildCsp("n", true);
    expect(csp).toContain("'unsafe-eval'");
    expect(csp).toContain("ws:");
    expect(csp).not.toContain("upgrade-insecure-requests");
  });

  it("gera nonce novo e em base64 a cada chamada", () => {
    const a = newNonce();
    expect(a).toMatch(/^[A-Za-z0-9+/]{22}==$/);
    expect(newNonce()).not.toBe(a);
  });
});
