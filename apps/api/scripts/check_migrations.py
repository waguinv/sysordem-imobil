"""Verifica que as migrations sobem e descem (rollback de schema) num banco descartável.

Uso: TEST_ADMIN_DATABASE_URL=postgresql+psycopg://superusuario:senha@host:5432/postgres \
     python -m scripts.check_migrations

1. cria o banco `imobil_migcheck` (UTF-8) e o papel da aplicação, se faltar;
2. `alembic upgrade head` → `downgrade base` → `upgrade head`, conferindo a revisão e que o
   downgrade não deixou tabelas para trás;
3. confirma que toda tabela com `tenant_id` tem RLS ligado e forçado;
4. apaga o banco.
"""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

DB = "imobil_migcheck"
APP_ROLE = os.environ.get("APP_DB_ROLE", "imobil_app_migcheck")
API_DIR = Path(__file__).resolve().parents[1]


def alembic(*args: str, env: dict[str, str]) -> str:
    r = subprocess.run(  # noqa: S603 - argumentos fixos deste script
        [sys.executable, "-m", "alembic", *args], cwd=API_DIR, env=env, capture_output=True, text=True
    )
    if r.returncode != 0:
        sys.stderr.write(r.stdout + r.stderr)
        raise SystemExit(f"alembic {' '.join(args)} falhou")
    return r.stdout.strip()


def main() -> None:
    admin_url = os.environ.get("TEST_ADMIN_DATABASE_URL")
    if not admin_url:
        raise SystemExit("Defina TEST_ADMIN_DATABASE_URL (superusuário)")
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))
        if not c.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}).scalar():
            c.execute(text(f"CREATE ROLE {APP_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS"))
        c.execute(
            text(f"CREATE DATABASE \"{DB}\" ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0")
        )

    url = make_url(admin_url).set(database=DB).render_as_string(hide_password=False)
    env = {
        **os.environ,
        "MIGRATION_DATABASE_URL": url,
        "DATABASE_URL": url,
        "APP_DB_ROLE": APP_ROLE,
        "JWT_SECRET": os.environ.get("JWT_SECRET", "migcheck-only-secret-0123456789abcdef"),
        "ENVIRONMENT": "test",
    }
    eng = create_engine(url)
    try:
        alembic("upgrade", "head", env=env)
        head = alembic("heads", env=env).split()[0]
        print(f"upgrade head: ok ({head})")

        alembic("downgrade", "base", env=env)
        with eng.connect() as c:
            left = c.execute(
                text(
                    "SELECT string_agg(tablename, ', ') FROM pg_tables "
                    "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                )
            ).scalar()
        if left:
            raise SystemExit(f"downgrade base deixou tabelas: {left}")
        print("downgrade base: ok (nenhuma tabela restante)")

        alembic("upgrade", "head", env=env)
        with eng.connect() as c:
            rev = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
            no_rls = c.execute(
                text(
                    "SELECT string_agg(c.relname, ', ') FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r' "
                    "AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid "
                    "            AND a.attname = 'tenant_id' AND NOT a.attisdropped) "
                    "AND NOT (c.relrowsecurity AND c.relforcerowsecurity)"
                )
            ).scalar()
        if rev != head:
            raise SystemExit(f"revisão após reaplicar: {rev} (esperado {head})")
        if no_rls:
            raise SystemExit(f"tabelas com tenant_id sem RLS forçado: {no_rls}")
        print("upgrade head de novo: ok; RLS forçado em todas as tabelas com tenant_id")
    finally:
        eng.dispose()
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))
        admin.dispose()


if __name__ == "__main__":
    main()
