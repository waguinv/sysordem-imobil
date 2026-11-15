"""Adaptadores de assinatura eletrônica: Documenso (real) e simulador (fora de produção)."""

from app.core.config import get_settings
from app.services.esign.base import SignatureProvider
from app.services.esign.documenso import DocumensoProvider
from app.services.esign.sandbox import SandboxSignatureProvider

_SANDBOX = SandboxSignatureProvider()
_DOCUMENSO = DocumensoProvider()


def available() -> list[SignatureProvider]:
    s = get_settings()
    out: list[SignatureProvider] = []
    if s.documenso_configured:
        out.append(_DOCUMENSO)
    if s.esign_sandbox:
        out.append(_SANDBOX)
    return out


def get_provider(name: str) -> SignatureProvider | None:
    return next((p for p in available() if p.name == name), None)


def default_provider() -> SignatureProvider | None:
    providers = available()
    return providers[0] if providers else None


def sandbox() -> SandboxSignatureProvider:
    return _SANDBOX
