"""Limites por IP do visitante (em memória do processo; com várias réplicas, mover para Redis).

Baldes:
- "login": ERROS de login no painel, no código do MFA, no portal do comprador e em links de senha/convite
  inválidos. Login certo não zera (senão o atacante intercalaria um login próprio para limpar os erros).
- "senha": pedidos de "esqueci minha senha".
- "cadastro": cadastros de empresa.
- "contato": envios do formulário de contato do site.

Sem IP (CLIENT_IP_SECRET não configurado ou sem assinatura), nada é limitado aqui; os limites por e-mail,
por usuário e por empresa continuam valendo.
"""

import time
from collections import defaultdict, deque
from threading import Lock

from app.core.config import get_settings

MAX_KEYS = 50_000

_hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_lock = Lock()


def _limits(bucket: str) -> tuple[int, float]:
    s = get_settings()
    return {
        "login": (s.ip_login_failures, 15 * 60.0),
        "senha": (s.ip_forgot_per_hour, 3600.0),
        "cadastro": (s.ip_signup_per_hour, 3600.0),
        "contato": (s.ip_leads_per_hour, 3600.0),
    }[bucket]


def _window(bucket: str, ip: str, now: float) -> deque[float]:
    limit, window = _limits(bucket)
    q = _hits[(bucket, ip)]
    while q and now - q[0] > window:
        q.popleft()
    return q


def blocked(bucket: str, ip: str | None) -> bool:
    if not ip:
        return False
    limit, _ = _limits(bucket)
    with _lock:
        return len(_window(bucket, ip, time.monotonic())) >= limit


def hit(bucket: str, ip: str | None) -> None:
    if not ip:
        return
    now = time.monotonic()
    with _lock:
        if len(_hits) > MAX_KEYS:  # proteção de memória: descarta janelas vazias
            for k in [k for k, q in _hits.items() if not q]:
                del _hits[k]
        _window(bucket, ip, now).append(now)


def take(bucket: str, ip: str | None) -> bool:
    """Para baldes que contam TODO pedido: registra e diz se ainda está dentro do limite."""
    if not ip:
        return True
    limit, _ = _limits(bucket)
    now = time.monotonic()
    with _lock:
        q = _window(bucket, ip, now)
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def reset() -> None:
    with _lock:
        _hits.clear()
