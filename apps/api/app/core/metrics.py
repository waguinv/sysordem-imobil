"""Contadores de saúde da API (em memória do processo), lidos pela rota interna /ops/status.

Janela deslizante de 15 min: requisições, respostas 5xx e respostas lentas. Cada worker do uvicorn tem
os seus contadores (com WEB_CONCURRENCY=1, que é o padrão do servidor, é o processo inteiro).
"""

import time
from collections import deque
from dataclasses import dataclass
from threading import Lock

WINDOW_SECONDS = 15 * 60
SLOW_MS = 2000.0

_started = time.monotonic()
_lock = Lock()
_requests: deque[float] = deque()
_errors: deque[float] = deque()
_slow: deque[float] = deque()


def _prune(q: deque[float], now: float) -> None:
    while q and now - q[0] > WINDOW_SECONDS:
        q.popleft()


def record(status: int, duration_ms: float, now: float | None = None) -> None:
    t = time.monotonic() if now is None else now
    with _lock:
        _requests.append(t)
        if status >= 500:
            _errors.append(t)
        if duration_ms >= SLOW_MS:
            _slow.append(t)
        for q in (_requests, _errors, _slow):
            _prune(q, t)


@dataclass(frozen=True)
class Snapshot:
    uptime_seconds: int
    requests: int
    errors_5xx: int
    slow: int


def snapshot(now: float | None = None) -> Snapshot:
    t = time.monotonic() if now is None else now
    with _lock:
        for q in (_requests, _errors, _slow):
            _prune(q, t)
        return Snapshot(int(t - _started), len(_requests), len(_errors), len(_slow))


def reset() -> None:
    with _lock:
        _requests.clear()
        _errors.clear()
        _slow.clear()
