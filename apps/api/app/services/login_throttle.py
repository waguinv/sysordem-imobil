"""Limite simples de tentativas de login por (empresa, e-mail) em memória do processo.

Suficiente para desenvolvimento e instância única. Em produção com várias réplicas,
substituir por armazenamento compartilhado (ex.: Redis) — ver docs/SEGURANCA.md.
"""

import time
from collections import defaultdict, deque
from threading import Lock

from app.core.config import get_settings

_failures: dict[str, deque[float]] = defaultdict(deque)
_lock = Lock()


def _key(tenant_slug: str, email: str) -> str:
    return f"{tenant_slug.lower()}|{email.lower()}"


def _prune(q: deque[float], now: float, window: int) -> None:
    while q and now - q[0] > window:
        q.popleft()


def is_blocked(tenant_slug: str, email: str) -> bool:
    s = get_settings()
    now = time.monotonic()
    with _lock:
        q = _failures[_key(tenant_slug, email)]
        _prune(q, now, s.login_window_seconds)
        return len(q) >= s.login_max_failures


def register_failure(tenant_slug: str, email: str) -> None:
    with _lock:
        _failures[_key(tenant_slug, email)].append(time.monotonic())


def reset(tenant_slug: str | None = None, email: str | None = None) -> None:
    with _lock:
        if tenant_slug is None or email is None:
            _failures.clear()
        else:
            _failures.pop(_key(tenant_slug, email), None)
