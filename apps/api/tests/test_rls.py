"""Row-Level Security direto no PostgreSQL, conectado como o papel da aplicação.

Prova que, mesmo se uma consulta esquecer o filtro por tenant, o banco não devolve
nem aceita linhas de outro tenant, e que o contexto não vaza entre transações do pool.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

pytestmark = pytest.mark.db


def _set(conn, tid):
    conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tid) if tid else ""})


def test_app_role_is_not_superuser_nor_bypassrls(app_engine):
    with app_engine.connect() as c:
        row = c.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
    assert tuple(row) == (False, False)


def test_without_context_no_rows_visible(app_engine, world):
    with app_engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM memberships")).scalar() == 0


def test_unfiltered_query_sees_only_context_tenant(app_engine, world):
    with app_engine.begin() as c:
        _set(c, world.tenant_a)
        tenants = set(c.execute(text("SELECT DISTINCT tenant_id FROM memberships")).scalars())
    assert tenants == {world.tenant_a}


def test_insert_into_other_tenant_rejected(app_engine, world):
    with pytest.raises(ProgrammingError, match="row-level security"), app_engine.begin() as c:
        _set(c, world.tenant_a)
        c.execute(
            text("INSERT INTO memberships (id, tenant_id, user_id, role) VALUES (:i,:t,:u,'leitura')"),
            {"i": uuid.uuid4(), "t": world.tenant_b, "u": world.users["alice"]},
        )


def test_update_cannot_move_row_to_other_tenant(app_engine, world):
    with pytest.raises(ProgrammingError, match="row-level security"), app_engine.begin() as c:
        _set(c, world.tenant_a)
        c.execute(
            text("UPDATE memberships SET tenant_id = :b WHERE id = :m"),
            {"b": world.tenant_b, "m": world.members[("alice", "a")]},
        )


def test_update_of_other_tenant_rows_affects_nothing(app_engine, world):
    with app_engine.begin() as c:
        _set(c, world.tenant_a)
        n = c.execute(
            text("UPDATE memberships SET is_active = false WHERE tenant_id = :b"), {"b": world.tenant_b}
        ).rowcount
    assert n == 0


def test_context_is_transaction_local_and_does_not_leak(app_engine, world):
    with app_engine.connect() as c:
        with c.begin():
            _set(c, world.tenant_a)
            assert c.execute(text("SELECT count(*) FROM memberships")).scalar() > 0
        with c.begin():  # mesma conexão física, nova transação
            assert c.execute(text("SELECT current_setting('app.tenant_id', true)")).scalar() in ("", None)
            assert c.execute(text("SELECT count(*) FROM memberships")).scalar() == 0


def test_audit_events_are_append_only(app_engine, world):
    with app_engine.begin() as c:
        _set(c, world.tenant_a)
        c.execute(
            text("INSERT INTO audit_events (id, tenant_id, action, entity) VALUES (:i,:t,'x','y')"),
            {"i": uuid.uuid4(), "t": world.tenant_a},
        )
    for stmt in ("UPDATE audit_events SET reason = 'alterado'", "DELETE FROM audit_events"):
        with pytest.raises(DBAPIError, match="permission denied"), app_engine.begin() as c:
            _set(c, world.tenant_a)
            c.execute(text(stmt))
