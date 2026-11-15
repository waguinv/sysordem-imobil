"""Teste de carga e de concorrência da API (Fase 6b). Só usa a API HTTP; nunca rode contra produção.

    python -m scripts.load_test --base-url http://127.0.0.1:8000 --users 20 --duration 30

1. Prepara (idempotente) um empreendimento "Carga 6b" com quadra e lotes e N compradores na empresa de teste.
2. **Corrida**: para cada lote do teste, `--race` requisições simultâneas tentam reservar o MESMO lote com
   compradores diferentes — exatamente uma pode vencer (201); as outras devem receber 409.
3. **Carga mista** por `--duration` segundos com `--users` clientes simultâneos: mapa, lote, listas e cadastro
   de comprador (10% de escrita).
4. Relatório por rota (p50/p95/p99, erros); saída ≠ 0 se a corrida falhar ou p95/erros passarem do limite.
"""

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

import httpx

# Sorteio de rotas/compradores do teste, não criptografia.
# ruff: noqa: S311

DEV_NAME = "Carga 6b"


@dataclass
class Stats:
    samples: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    errors: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    statuses: dict[str, dict[int, int]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))

    def add(self, name: str, ms: float, status: int, ok: bool) -> None:
        self.samples[name].append(ms)
        self.statuses[name][status] += 1
        if not ok:
            self.errors[name] += 1


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = min(len(s) - 1, max(0, round(p / 100 * (len(s) - 1))))
    return s[k]


async def timed(c: httpx.AsyncClient, st: Stats, name: str, method: str, url: str, ok=(200,), **kw):
    t = time.perf_counter()
    try:
        r = await c.request(method, url, **kw)
        status = r.status_code
    except httpx.HTTPError:
        r, status = None, 0
    st.add(name, (time.perf_counter() - t) * 1000, status, status in ok)
    return r


async def login(c: httpx.AsyncClient, tenant: str, email: str, password: str) -> str:
    r = await c.post("/api/v1/auth/login", json={"tenant_slug": tenant, "email": email, "password": password})
    if r.status_code != 200:
        sys.exit(f"login falhou: {r.status_code} {r.text[:200]}")
    return str(r.json()["access_token"])


async def setup(c: httpx.AsyncClient, lots: int, customers: int) -> tuple[str, list[str], list[str]]:
    devs = (await c.get("/api/v1/developments")).json()
    dev = next((d for d in devs if d["name"] == DEV_NAME), None)
    if dev is None:
        r = await c.post(
            "/api/v1/developments", json={"name": DEV_NAME, "status": "em_vendas", "city": "Teste"}
        )
        if r.status_code != 201:
            sys.exit(f"não criou empreendimento: {r.status_code} {r.text[:300]}")
        dev = r.json()
    dev_id = dev["id"]
    m = (await c.get(f"/api/v1/developments/{dev_id}/map")).json()
    blocks = m.get("blocks", [])
    if not blocks:
        b = (await c.post(f"/api/v1/developments/{dev_id}/blocks", json={"code": "A"})).json()
        r = await c.post(
            f"/api/v1/developments/{dev_id}/lots/batch",
            json={"block_id": b["id"], "first": 1, "last": lots, "area_m2": "300", "price": "90000"},
        )
        if r.status_code != 201:
            sys.exit(f"não criou lotes: {r.status_code} {r.text[:300]}")
        m = (await c.get(f"/api/v1/developments/{dev_id}/map")).json()
        blocks = m["blocks"]
    free = [lot["id"] for blk in blocks for lot in blk["lots"] if lot["status"] == "disponivel"]

    existing = (await c.get("/api/v1/customers", params={"q": "Carga"})).json()
    cust = [x["id"] for x in existing if x["name"].startswith("Comprador Carga")]
    for i in range(len(cust), customers):
        r = await c.post("/api/v1/customers", json={"name": f"Comprador Carga {i:03d}", "city": "Teste"})
        if r.status_code != 201:
            sys.exit(f"não criou comprador: {r.status_code} {r.text[:300]}")
        cust.append(r.json()["id"])
    return dev_id, free, cust


async def race(c: httpx.AsyncClient, st: Stats, lot_id: str, customers: list[str], n: int) -> tuple[int, int]:
    picks = random.sample(customers, min(n, len(customers)))
    rs = await asyncio.gather(
        *[
            timed(
                c,
                st,
                "POST /reservations (corrida)",
                "POST",
                "/api/v1/reservations",
                ok=(201, 409),
                json={"lot_id": lot_id, "customer_id": cid, "hours": 1},
            )
            for cid in picks
        ]
    )
    won = sum(1 for r in rs if r is not None and r.status_code == 201)
    others = sum(1 for r in rs if r is not None and r.status_code not in (201, 409))
    for r in rs:  # libera o lote para a próxima execução
        if r is not None and r.status_code == 201:
            await c.post(f"/api/v1/reservations/{r.json()['id']}/cancel", json={"reason": "teste de carga"})
    return won, others


async def worker(c: httpx.AsyncClient, st: Stats, dev_id: str, lots: list[str], until: float) -> None:
    while time.perf_counter() < until:
        roll = random.random()
        if roll < 0.30:
            await timed(c, st, "GET /developments/{id}/map", "GET", f"/api/v1/developments/{dev_id}/map")
        elif roll < 0.50:
            await timed(c, st, "GET /lots/{id}", "GET", f"/api/v1/lots/{random.choice(lots)}")
        elif roll < 0.65:
            await timed(c, st, "GET /reservations", "GET", "/api/v1/reservations")
        elif roll < 0.80:
            await timed(c, st, "GET /customers", "GET", "/api/v1/customers")
        elif roll < 0.90:
            await timed(c, st, "GET /developments", "GET", "/api/v1/developments")
        else:
            await timed(
                c,
                st,
                "POST /customers",
                "POST",
                "/api/v1/customers",
                ok=(201,),
                json={"name": f"Visitante {uuid.uuid4().hex[:8]}", "city": "Teste"},
            )


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--tenant", default="demo")
    ap.add_argument("--email", default="admin@demo.test")
    ap.add_argument("--users", type=int, default=20)
    ap.add_argument("--duration", type=float, default=30)
    ap.add_argument("--race", type=int, default=25, help="requisições simultâneas por lote na corrida")
    ap.add_argument("--race-lots", type=int, default=5)
    ap.add_argument("--max-p95-ms", type=float, default=800)
    ap.add_argument("--max-error-rate", type=float, default=0.01)
    ap.add_argument("--json", help="grava o resumo em JSON neste arquivo")
    a = ap.parse_args()
    password = os.environ.get("LOAD_PASSWORD") or __import__("scripts.seed_dev", fromlist=["x"]).DEV_PASSWORD

    limits = httpx.Limits(max_connections=a.users + a.race, max_keepalive_connections=a.users + a.race)
    async with httpx.AsyncClient(base_url=a.base_url, timeout=30, limits=limits) as c:
        c.headers["Authorization"] = f"Bearer {await login(c, a.tenant, a.email, password)}"
        dev_id, free, customers = await setup(c, lots=40, customers=max(a.race, 30))
        if len(free) < a.race_lots:
            sys.exit(f"poucos lotes livres para a corrida ({len(free)})")
        st = Stats()

        race_ok = True
        for lot_id in free[: a.race_lots]:
            won, others = await race(c, st, lot_id, customers, a.race)
            print(f"corrida lote {lot_id[:8]}: {won} venceu, {others} respostas inesperadas")
            race_ok &= won == 1 and others == 0

        t0 = time.perf_counter()
        until = t0 + a.duration
        await asyncio.gather(*[worker(c, st, dev_id, free, until) for _ in range(a.users)])
        elapsed = time.perf_counter() - t0

    total = sum(len(v) for k, v in st.samples.items() if "corrida" not in k)
    errs = sum(v for k, v in st.errors.items() if "corrida" not in k)
    all_ms = [ms for k, v in st.samples.items() if "corrida" not in k for ms in v]
    summary = {
        "users": a.users,
        "duration_s": round(elapsed, 1),
        "requests": total,
        "rps": round(total / elapsed, 1) if elapsed else 0,
        "error_rate": round(errs / total, 4) if total else 0,
        "p50_ms": round(pct(all_ms, 50), 1),
        "p95_ms": round(pct(all_ms, 95), 1),
        "p99_ms": round(pct(all_ms, 99), 1),
        "race_ok": race_ok,
        "routes": {
            k: {
                "n": len(v),
                "p50": round(pct(v, 50), 1),
                "p95": round(pct(v, 95), 1),
                "p99": round(pct(v, 99), 1),
                "mean": round(statistics.fmean(v), 1),
                "errors": st.errors.get(k, 0),
                "status": dict(st.statuses[k]),
            }
            for k, v in sorted(st.samples.items())
        },
    }
    print(f"\n{'rota':34} {'n':>6} {'p50':>7} {'p95':>7} {'p99':>7} {'erros':>6}")
    for k, r in summary["routes"].items():
        print(f"{k:34} {r['n']:>6} {r['p50']:>7} {r['p95']:>7} {r['p99']:>7} {r['errors']:>6}")
    print(
        f"\ntotal {total} req em {summary['duration_s']} s = {summary['rps']} req/s; "
        f"p50 {summary['p50_ms']} ms, p95 {summary['p95_ms']} ms, p99 {summary['p99_ms']} ms; "
        f"erros {summary['error_rate']:.2%}; corrida {'ok' if race_ok else 'FALHOU'}"
    )
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

    failed = []
    if not race_ok:
        failed.append("corrida: mais de uma reserva para o mesmo lote ou resposta inesperada")
    if summary["p95_ms"] > a.max_p95_ms:
        failed.append(f"p95 {summary['p95_ms']} ms > {a.max_p95_ms}")
    if summary["error_rate"] > a.max_error_rate:
        failed.append(f"erros {summary['error_rate']:.2%} > {a.max_error_rate:.2%}")
    for f_ in failed:
        print("FALHOU:", f_)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
