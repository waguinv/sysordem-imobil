"""Adaptadores de pagamento das EMPRESAS (parcelas dos compradores).

- Asaas: a conta Asaas da própria empresa (Configurações → Recebimentos). Quando ligada, é o provedor
  padrão das novas cobranças daquela empresa.
- Sandbox: simulador, fora de produção por padrão.

O provedor de uma cobrança já emitida é sempre o gravado nela (`charges.provider`).
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import credentials
from app.core.config import get_settings
from app.models import PaymentAccount
from app.services.psp.asaas import AsaasClient, AsaasProvider
from app.services.psp.base import PaymentProvider, ProviderError
from app.services.psp.sandbox import SandboxProvider

log = logging.getLogger("imobil.psp")

_SANDBOX = SandboxProvider()
LABELS = {"sandbox": _SANDBOX.label, "asaas": "Asaas"}
API_KEY_PURPOSE = "psp-api-key"


def label(name: str) -> str:
    return LABELS.get(name, name)


def account(db: Session, tenant_id: uuid.UUID) -> PaymentAccount | None:
    return db.scalar(select(PaymentAccount).where(PaymentAccount.tenant_id == tenant_id))


def provider_for_account(acct: PaymentAccount) -> AsaasProvider:
    """Levanta ProviderError se a chave guardada não abre (PAYMENTS_CREDENTIALS_KEY trocada/ausente)."""
    try:
        api_key = credentials.open_sealed(
            acct.api_key_sealed, tenant_id=acct.tenant_id, purpose=API_KEY_PURPOSE
        )
    except credentials.CredentialsUnavailableError as e:
        log.error("credencial do PSP da empresa %s indisponível: %s", acct.tenant_id, e)
        raise ProviderError("Credencial do Asaas indisponível neste servidor — fale com o suporte") from None
    return AsaasProvider(
        AsaasClient(api_key, acct.environment), webhook_token_sha256=acct.webhook_token_sha256
    )


def _tenant_asaas(db: Session | None, tenant_id: uuid.UUID | None) -> AsaasProvider | None:
    if db is None or tenant_id is None:
        return None
    acct = account(db, tenant_id)
    return provider_for_account(acct) if acct else None


def available(db: Session | None = None, tenant_id: uuid.UUID | None = None) -> list[PaymentProvider]:
    """Provedores habilitados para a empresa (o primeiro é o padrão das novas cobranças)."""
    out: list[PaymentProvider] = []
    try:
        a = _tenant_asaas(db, tenant_id)
    except ProviderError:
        a = None
    if a is not None:
        out.append(a)
    if get_settings().sandbox_enabled:
        out.append(_SANDBOX)
    return out


def get_provider(
    name: str, db: Session | None = None, tenant_id: uuid.UUID | None = None
) -> PaymentProvider | None:
    if name == "asaas":
        return _tenant_asaas(db, tenant_id)
    if name == _SANDBOX.name and get_settings().sandbox_enabled:
        return _SANDBOX
    return None


def default_provider(db: Session, tenant_id: uuid.UUID) -> PaymentProvider | None:
    """Conta Asaas da empresa, se ligada (erro se a credencial não abrir); senão o sandbox, se habilitado."""
    a = _tenant_asaas(db, tenant_id)
    if a is not None:
        return a
    return _SANDBOX if get_settings().sandbox_enabled else None
