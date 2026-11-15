"""Regressão: a sessão de banco precisa fazer COMMIT antes de a resposta ser enviada.

No FastAPI ≥ 0.118 o código após o `yield` de uma dependência roda depois da resposta, a menos
que ela seja declarada com scope="function". Sem isso, o cliente recebe 201 antes do COMMIT.
Verifica TODOS os routers de `app.api.v1` (inclusive os criados em fases futuras).
"""

import importlib
import pkgutil

from fastapi import APIRouter
from fastapi.routing import APIRoute

import app.api.v1 as v1_pkg
from app.core.db import get_db


def _walk(dependant):
    yield dependant
    for d in dependant.dependencies:
        yield from _walk(d)


def _routers() -> list[APIRouter]:
    out = []
    for mod in pkgutil.iter_modules(v1_pkg.__path__):
        m = importlib.import_module(f"{v1_pkg.__name__}.{mod.name}")
        out += [v for v in vars(m).values() if isinstance(v, APIRouter)]
    return out


def test_every_get_db_dependency_has_function_scope():
    routers = _routers()
    assert len(routers) >= 15
    found = 0
    for router in routers:
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            for dep in _walk(route.dependant):
                if dep.call is get_db:
                    found += 1
                    assert dep.scope == "function", f"{route.path}: get_db sem scope='function'"
    assert found >= 100
