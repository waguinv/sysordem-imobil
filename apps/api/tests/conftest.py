"""Fixtures de teste.

Testes marcados `db` exigem PostgreSQL. Defina TEST_ADMIN_DATABASE_URL apontando para um
superusuário (ex.: postgresql+psycopg://imobil_owner:SENHA@localhost:5432/postgres).
O conftest cria o banco `imobil_test`, o papel `imobil_app_test` (sem superuser/BYPASSRLS),
aplica as migrations e faz a aplicação conectar como esse papel — então o RLS é exercitado de verdade.
"""

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ADMIN_URL = os.environ.get("TEST_ADMIN_DATABASE_URL")
TEST_DB = "imobil_test"
APP_ROLE = "imobil_app_test"
APP_PASSWORD = "test_only_app_password"  # fictícia, só para o banco de teste

os.environ["JWT_SECRET"] = "test-only-secret-with-at-least-32-characters!!"
os.environ["ENVIRONMENT"] = "test"
os.environ["APP_DB_ROLE"] = APP_ROLE

if ADMIN_URL:
    _admin = make_url(ADMIN_URL)
    OWNER_TEST_URL = _admin.set(database=TEST_DB).render_as_string(hide_password=False)
    APP_TEST_URL = _admin.set(database=TEST_DB, username=APP_ROLE, password=APP_PASSWORD).render_as_string(
        hide_password=False
    )
    os.environ["DATABASE_URL"] = APP_TEST_URL
    os.environ["MIGRATION_DATABASE_URL"] = OWNER_TEST_URL

API_DIR = Path(__file__).resolve().parents[1]
PASSWORD = "senha-de-teste-123"


def pytest_collection_modifyitems(config, items):
    if ADMIN_URL:
        return
    skip = pytest.mark.skip(reason="TEST_ADMIN_DATABASE_URL não definido")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def database() -> Iterator[None]:
    assert ADMIN_URL
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
        exists = c.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}).scalar()
        verb = "ALTER" if exists else "CREATE"
        c.execute(text(f"{verb} ROLE {APP_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '{APP_PASSWORD}'"))
        # UTF-8 explícito: no Windows o padrão do initdb é WIN1252 e o driver lê JSONB como UTF-8.
        c.execute(
            text(
                f"CREATE DATABASE \"{TEST_DB}\" ENCODING 'UTF8' "
                "LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0"
            )
        )
    admin.dispose()
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=API_DIR, check=True, env={**os.environ}
    )
    yield


@pytest.fixture(scope="session")
def owner_engine(database):
    eng = create_engine(OWNER_TEST_URL)
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def app_engine(database):
    eng = create_engine(APP_TEST_URL)
    yield eng
    eng.dispose()


class World:
    """Dois tenants (A e B) e usuários com perfis distintos."""

    def __init__(self) -> None:
        self.tenant_a = uuid.uuid4()
        self.tenant_b = uuid.uuid4()
        self.users: dict[str, uuid.UUID] = {}
        self.members: dict[tuple[str, str], uuid.UUID] = {}


SPEC = {
    "alice": [("a", "owner")],
    "carol": [("a", "leitura")],
    "erin": [("a", "admin")],
    "bob": [("b", "owner")],
    "dave": [("a", "comercial"), ("b", "leitura")],
    "fabi": [("a", "financeiro")],
    "gil": [("a", "corretor")],
}


@pytest.fixture()
def world(owner_engine) -> World:
    from app.core.security import hash_password
    from app.services import login_throttle

    login_throttle.reset()
    w = World()
    pw = hash_password(PASSWORD)
    with owner_engine.begin() as c:
        c.execute(
            text(
                "TRUNCATE contract_policies, contract_versions, sales_contracts, tenant_counters, "
                "reservations, customers, brokers, "
                "lot_status_history, lots, blocks, developments, land_owners, "
                "audit_events, memberships, users, tenants CASCADE"
            )
        )
        c.execute(
            text(
                "INSERT INTO tenants (id, slug, name) "
                "VALUES (:a,'empresa-a','Empresa A'),(:b,'empresa-b','Empresa B')"
            ),
            {"a": w.tenant_a, "b": w.tenant_b},
        )
        for name, links in SPEC.items():
            uid = uuid.uuid4()
            w.users[name] = uid
            c.execute(
                text("INSERT INTO users (id,email,full_name,password_hash) VALUES (:i,:e,:n,:p)"),
                {"i": uid, "e": f"{name}@example.com", "n": name.title(), "p": pw},
            )
            for t, role in links:
                tid = w.tenant_a if t == "a" else w.tenant_b
                mid = uuid.uuid4()
                w.members[(name, t)] = mid
                c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tid)})
                c.execute(
                    text("INSERT INTO memberships (id,tenant_id,user_id,role) VALUES (:m,:t,:u,:r)"),
                    {"m": mid, "t": tid, "u": uid, "r": role},
                )
    return w


@pytest.fixture()
def client(world):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def login(client, user: str, tenant: str = "empresa-a") -> dict[str, str]:
    r = client.post(
        "/api/v1/auth/login",
        json={"tenant_slug": tenant, "email": f"{user}@example.com", "password": PASSWORD},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
