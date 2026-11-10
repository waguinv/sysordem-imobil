"""Regressão: a sessão de banco precisa fazer COMMIT antes de a resposta ser enviada.

No FastAPI ≥ 0.118 o código após o `yield` de uma dependência roda depois da resposta, a menos
que ela seja declarada com scope="function". Sem isso, o cliente recebe 201 antes do COMMIT.
"""

from fastapi.routing import APIRoute

from app.api.v1 import auth, commercial, developments, tenant
from app.core.db import get_db


def _walk(dependant):
    yield dependant
    for d in dependant.dependencies:
        yield from _walk(d)


def test_every_get_db_dependency_has_function_scope():
    found = 0
    for router in (auth.router, tenant.router, developments.router, commercial.router):
        for route in router.routes:
            assert isinstance(route, APIRoute)
            for dep in _walk(route.dependant):
                if dep.call is get_db:
                    found += 1
                    assert dep.scope == "function", f"{route.path}: get_db sem scope='function'"
    assert found >= 20
