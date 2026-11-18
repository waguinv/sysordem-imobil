"""Camada HTTP transversal (Fase 6a): id de requisição, cabeçalhos de segurança, limite de corpo,
limite de taxa nos webhooks públicos e log de acesso estruturado (sem dados pessoais).

Middleware ASGI puro (sem BaseHTTPMiddleware) para não bufferizar respostas nem quebrar streaming.
"""

import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, MutableMapping
from contextvars import ContextVar
from typing import Any

from app.core import metrics

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
access_log = logging.getLogger("imobil.access")

_RID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
WEBHOOK_PREFIX = "/api/v1/webhooks/"


class RateLimiter:
    """Janela deslizante por chave, em memória do processo (com várias réplicas, mover para Redis)."""

    def __init__(self, limit: int, window_seconds: float = 60.0, max_keys: int = 10_000) -> None:
        self.limit = limit
        self.window = window_seconds
        self.max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, now: float | None = None) -> float | None:
        """Registra uma chamada. Devolve None se permitida, ou os segundos até liberar."""
        now = time.monotonic() if now is None else now
        cutoff = now - self.window
        with self._lock:
            q = self._hits.get(key)
            if q is None:
                if len(self._hits) >= self.max_keys:
                    self._prune(cutoff)
                q = self._hits.setdefault(key, deque())
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= self.limit:
                return max(0.0, q[0] + self.window - now)
            q.append(now)
            return None

    def _prune(self, cutoff: float) -> None:
        for k in [k for k, q in self._hits.items() if not q or q[-1] <= cutoff]:
            del self._hits[k]
        if (
            len(self._hits) >= self.max_keys
        ):  # ainda cheio: descarta tudo (falha aberta, sem crescer sem limite)
            self._hits.clear()

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def _headers_for(path: str, production: bool) -> list[tuple[bytes, bytes]]:
    h = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
        (b"cross-origin-resource-policy", b"same-origin"),
    ]
    if path.startswith("/api/") or path in ("/health", "/ready"):
        # JSON/PDF da API nunca devem ser executados nem embutidos pelo navegador
        h.append((b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"))
    if production:
        h.append((b"strict-transport-security", b"max-age=63072000; includeSubDomains"))
    return h


class SecurityMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        production: bool,
        max_body_bytes: int,
        webhook_limiter: RateLimiter | None,
    ) -> None:
        self.app = app
        self.production = production
        self.max_body = max_body_bytes
        self.webhook_limiter = webhook_limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        path: str = scope.get("path", "")
        incoming = _header(scope, b"x-request-id")
        rid = incoming if incoming and _RID_RE.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(rid)
        extra = [(b"x-request-id", rid.encode()), *_headers_for(path, self.production)]
        status_holder = {"status": 500, "started": False}

        async def send_wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                status_holder["started"] = True
                existing = {k.lower() for k, _ in message.get("headers", [])}
                headers = list(message.get("headers", []))
                headers += [(k, v) for k, v in extra if k not in existing]
                if path.startswith("/api/") and b"cache-control" not in existing:
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        try:
            # 1) limite de taxa dos webhooks (chegam direto do provedor; o resto passa pelo BFF)
            if self.webhook_limiter and path.startswith(WEBHOOK_PREFIX) and scope.get("method") == "POST":
                client = scope.get("client")
                key = client[0] if client else "unknown"
                wait = self.webhook_limiter.hit(key)
                if wait is not None:
                    await _reject(send_wrapped, 429, "Muitas requisições", retry_after=int(wait) + 1)
                    return

            # 2) limite de tamanho do corpo (Content-Length e também o fluxo real)
            declared = _header(scope, b"content-length")
            if declared is not None:
                if not declared.isdigit():
                    await _reject(send_wrapped, 400, "Content-Length inválido")
                    return
                if int(declared) > self.max_body:
                    await _reject(send_wrapped, 413, "Corpo da requisição muito grande")
                    return

            received = 0
            too_large = False

            async def receive_limited() -> Message:
                nonlocal received, too_large
                message = await receive()
                if message["type"] == "http.request":
                    received += len(message.get("body", b""))
                    if received > self.max_body:
                        too_large = True
                        raise _BodyTooLargeError
                return message

            try:
                await self.app(scope, receive_limited, send_wrapped)
            except _BodyTooLargeError:
                if not status_holder["started"]:
                    await _reject(send_wrapped, 413, "Corpo da requisição muito grande")
            if too_large and status_holder["started"] and status_holder["status"] != 413:
                access_log.warning("corpo excedeu o limite após o início da resposta")
        finally:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
            metrics.record(status_holder["status"], elapsed_ms)  # saúde da API (/ops/status)
            access_log.info(
                "request",
                extra={
                    "http": {
                        "method": scope.get("method"),
                        # só o caminho: a query string pode ter e-mail/CPF
                        "path": path,
                        "status": status_holder["status"],
                        "duration_ms": elapsed_ms,
                    }
                },
            )
            request_id_var.reset(token)


class _BodyTooLargeError(Exception):
    pass


def _header(scope: Scope, name: bytes) -> str | None:
    for k, v in scope.get("headers", []):
        if k.lower() == name:
            return str(v.decode("latin-1"))
    return None


async def _reject(send: Send, status: int, detail: str, retry_after: int | None = None) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    if retry_after is not None:
        headers.append((b"retry-after", str(retry_after).encode()))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class JsonFormatter(logging.Formatter):
    """Uma linha JSON por evento. Não registra corpo, query string, tokens nem dados pessoais."""

    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        http = getattr(record, "http", None)
        if http:
            data["http"] = http
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False, default=str)


def configure_logging(fmt: str, level: str = "INFO") -> None:
    root = logging.getLogger()
    if getattr(root, "_imobil_configured", False):
        return
    handler = logging.StreamHandler()
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s%(http_text)s")
        )
        handler.addFilter(_RequestIdFilter())
    root.handlers = [handler]
    root.setLevel(level.upper())
    root._imobil_configured = True  # type: ignore[attr-defined]


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        http = getattr(record, "http", None)
        record.http_text = (
            f" {http['method']} {http['path']} {http['status']} {http['duration_ms']}ms" if http else ""
        )
        return True
