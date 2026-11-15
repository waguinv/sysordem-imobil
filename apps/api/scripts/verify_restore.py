"""Confere um banco restaurado contra o original (Fase 6b — teste de restauração de backup).

    python -m scripts.verify_restore --source imobil --target imobil_restore_ci

Conecta com MIGRATION_DATABASE_URL (papel dono), trocando só o nome do banco, e compara:
- revisão das migrations;
- para cada tabela: quantidade de linhas e um hash (md5) de todas as linhas em ordem;
- RLS ligado e forçado e as políticas de cada tabela;
- que o papel da aplicação (APP_DB_ROLE) continua sem ver nada sem empresa no contexto e vê exatamente as
  linhas da empresa quando o contexto é definido (o RLS funciona no banco restaurado).
Saída ≠ 0 se qualquer diferença aparecer.
"""

import argparse
import os
import sys

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

TABLES_SQL = text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
RLS_SQL = text(
    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
    "       coalesce((SELECT string_agg(p.polname || ':' || pg_get_expr(p.polqual, p.polrelid), ',' "
    "                 ORDER BY p.polname) FROM pg_policy p WHERE p.polrelid = c.oid), '') "
    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY c.relname"
)


def fingerprint(eng: Engine) -> dict[str, object]:
    out: dict[str, object] = {}
    with eng.connect() as c:
        out["revision"] = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
        for (t,) in c.execute(TABLES_SQL).all():
            # nomes vêm do catálogo do próprio banco; sem entrada de usuário
            n, h = c.execute(
                text(
                    "SELECT count(*), md5(coalesce(string_agg(x::text, '|' ORDER BY x::text), '')) "
                    f'FROM "{t}" x'
                )
            ).one()
            out[f"rows:{t}"] = (n, h)
        out["rls"] = [tuple(r) for r in c.execute(RLS_SQL).all()]
    return out


def rls_probe(eng: Engine, role: str) -> list[str]:
    """Com o papel da aplicação: sem contexto não vê nada; com contexto vê só a própria empresa."""
    problems: list[str] = []
    with eng.connect() as c:
        tenants = [r[0] for r in c.execute(text("SELECT id FROM tenants ORDER BY slug")).all()]
        tables = [
            r[0]
            for r in c.execute(
                text(
                    "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r' AND EXISTS (SELECT 1 FROM pg_attribute a "
                    "WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped) "
                    "ORDER BY 1"
                )
            ).all()
        ]
        expected = {
            (t, tid): c.execute(
                text(f'SELECT count(*) FROM "{t}" WHERE tenant_id = :tid'), {"tid": tid}
            ).scalar()
            for t in tables
            for tid in tenants
        }
        c.commit()  # encerra a transação de leitura acima antes de trocar de papel
        with c.begin():
            c.execute(text(f'SET LOCAL ROLE "{role}"'))
            for t in tables:
                if c.execute(text(f'SELECT count(*) FROM "{t}"')).scalar():
                    problems.append(f"{t}: papel da aplicação vê linhas sem empresa no contexto")
            for tid in tenants:
                c.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tid)})
                for t in tables:
                    seen = c.execute(text(f'SELECT count(*) FROM "{t}"')).scalar()
                    if seen != expected[(t, tid)]:
                        problems.append(f"{t}: empresa {tid} vê {seen}, esperado {expected[(t, tid)]}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--target", required=True)
    a = ap.parse_args()
    base = os.environ.get("MIGRATION_DATABASE_URL")
    if not base:
        sys.exit("Defina MIGRATION_DATABASE_URL (papel dono)")
    role = os.environ.get("APP_DB_ROLE", "imobil_app")
    url = make_url(base)
    src = create_engine(url.set(database=a.source))
    dst = create_engine(url.set(database=a.target))
    try:
        fs, fd = fingerprint(src), fingerprint(dst)
        problems = [
            f"{k}: original={fs.get(k)!r} restaurado={fd.get(k)!r}"
            for k in sorted(fs.keys() | fd.keys())
            if fs.get(k) != fd.get(k)
        ]
        problems += rls_probe(dst, role)
    finally:
        src.dispose()
        dst.dispose()
    tables = sum(1 for k in fs if k.startswith("rows:"))
    rows = sum(v[0] for k, v in fs.items() if k.startswith("rows:"))  # type: ignore[index]
    if problems:
        print("RESTAURAÇÃO DIVERGENTE:")
        for p in problems:
            print(" -", p)
        return 1
    print(
        f"restauração ok: revisão {fs['revision']}, {tables} tabelas, {rows} linhas idênticas; RLS conferido"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
