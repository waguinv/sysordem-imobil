"""Adaptadores de pagamento (Fase 4a). Hoje só o sandbox; o PSP real entra aqui quando escolhido."""

from app.core.config import get_settings
from app.services.psp.base import PaymentProvider
from app.services.psp.sandbox import SandboxProvider

_PROVIDERS: dict[str, PaymentProvider] = {"sandbox": SandboxProvider()}


def available() -> list[PaymentProvider]:
    """Provedores habilitados neste ambiente (sandbox fica fora de produção por padrão)."""
    s = get_settings()
    return [p for name, p in _PROVIDERS.items() if name != "sandbox" or s.sandbox_enabled]


def get_provider(name: str) -> PaymentProvider | None:
    return next((p for p in available() if p.name == name), None)


def default_provider() -> PaymentProvider | None:
    providers = available()
    return providers[0] if providers else None
