"""Conta Asaas da empresa (Configurações → Recebimentos).

Ligar: valida a chave na API do Asaas, registra o webhook NA CONTA DA EMPRESA (URL com o id da empresa
+ token aleatório guardado só como hash) e guarda a chave cifrada. Trocar a chave refaz tudo. A chave
nunca volta para a tela nem para a auditoria — só os 4 últimos caracteres.

Sem PUBLIC_API_URL (desenvolvimento), a conta é ligada sem webhook: a situação das cobranças é
atualizada pelo botão "Atualizar situação" (consulta direta ao Asaas).
"""

import logging
import secrets
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import credentials
from app.core.config import get_settings
from app.models import Charge, PaymentAccount
from app.services import audit, psp
from app.services.psp.asaas import AsaasClient, token_hash
from app.services.psp.base import ProviderError

log = logging.getLogger("imobil.psp")


class PaymentAccountError(Exception):
    """Mensagem pronta para o usuário."""


@dataclass(frozen=True)
class AccountStatus:
    configured: bool
    credentials_available: bool  # o servidor tem PAYMENTS_CREDENTIALS_KEY
    environments: list[str]
    provider: str | None = None
    environment: str | None = None
    account_name: str | None = None
    api_key_hint: str | None = None
    webhook: bool = False
    webhook_url: str | None = None
    pending_charges: int = 0


def _environments() -> list[str]:
    return ["production"] if get_settings().is_production else ["sandbox", "production"]


def _pending(db: Session, tenant_id: uuid.UUID) -> int:
    return (
        db.scalar(
            select(func.count()).where(
                Charge.tenant_id == tenant_id, Charge.provider == "asaas", Charge.status == "pendente"
            )
        )
        or 0
    )


def status(db: Session, tenant_id: uuid.UUID) -> AccountStatus:
    acct = psp.account(db, tenant_id)
    base = {"credentials_available": credentials.available(), "environments": _environments()}
    if acct is None:
        return AccountStatus(configured=False, **base)  # type: ignore[arg-type]
    return AccountStatus(
        configured=True,
        provider=acct.provider,
        environment=acct.environment,
        account_name=acct.account_name,
        api_key_hint=acct.api_key_hint,
        webhook=bool(acct.webhook_id and acct.webhook_token_sha256),
        webhook_url=acct.webhook_url,
        pending_charges=_pending(db, tenant_id),
        **base,  # type: ignore[arg-type]
    )


def _drop_webhook(acct: PaymentAccount) -> None:
    if not acct.webhook_id:
        return
    try:
        psp.provider_for_account(acct).client.delete_webhook(acct.webhook_id)
    except ProviderError:
        log.warning("webhook %s da empresa %s não foi removido no Asaas", acct.webhook_id, acct.tenant_id)


def connect(
    db: Session, *, tenant_id: uuid.UUID, api_key: str, environment: str, actor: uuid.UUID, actor_email: str
) -> AccountStatus:
    api_key = api_key.strip()
    if environment not in _environments():
        raise PaymentAccountError("Ambiente do Asaas não permitido aqui (produção só aceita a conta real)")
    if not credentials.available():
        raise PaymentAccountError("Servidor sem PAYMENTS_CREDENTIALS_KEY — fale com o suporte")
    if not 20 <= len(api_key) <= 400 or any(ch.isspace() for ch in api_key):
        raise PaymentAccountError("Chave de API do Asaas em formato inválido")
    acct = db.scalar(select(PaymentAccount).where(PaymentAccount.tenant_id == tenant_id).with_for_update())
    if acct is not None and acct.environment != environment and _pending(db, tenant_id):
        raise PaymentAccountError(
            "Há cobranças pendentes no Asaas desta empresa — cancele-as antes de trocar de ambiente"
        )
    client = AsaasClient(api_key, environment)
    try:
        name = client.account_name()
    except ProviderError as e:
        raise PaymentAccountError(f"O Asaas não aceitou a chave: {e}") from None

    webhook_id = token_sha = url = None
    base_url = get_settings().public_api_url
    if base_url:
        token = secrets.token_urlsafe(48)
        url = f"{base_url}/api/v1/webhooks/payments/asaas/{tenant_id}"
        try:
            webhook_id = client.create_webhook(url=url, token=token, email=actor_email)
        except ProviderError as e:
            raise PaymentAccountError(f"O Asaas não registrou o webhook: {e}") from None
        token_sha = token_hash(token)

    if acct is not None:
        _drop_webhook(acct)  # o webhook antigo (outra chave/conta) deixa de valer
    else:
        acct = PaymentAccount(tenant_id=tenant_id, provider="asaas")
        db.add(acct)
    acct.environment = environment
    acct.api_key_sealed = credentials.seal(api_key, tenant_id=tenant_id, purpose=psp.API_KEY_PURPOSE)
    acct.api_key_hint = f"…{api_key[-4:]}"
    acct.account_name = name
    acct.webhook_id = webhook_id
    acct.webhook_token_sha256 = token_sha
    acct.webhook_url = url
    acct.updated_by = actor
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="payment_account.connect",
        entity="payment_account",
        data={
            "provider": "asaas",
            "environment": environment,
            "webhook": bool(webhook_id),
            "key": acct.api_key_hint,
        },
    )
    return status(db, tenant_id)


def disconnect(db: Session, *, tenant_id: uuid.UUID, actor: uuid.UUID, reason: str) -> AccountStatus:
    acct = db.scalar(select(PaymentAccount).where(PaymentAccount.tenant_id == tenant_id).with_for_update())
    if acct is None:
        raise PaymentAccountError("Nenhuma conta de recebimento ligada")
    n = _pending(db, tenant_id)
    if n:
        raise PaymentAccountError(
            f"Há {n} cobrança(s) pendente(s) no Asaas — cancele-as antes de desligar a conta"
        )
    _drop_webhook(acct)
    db.delete(acct)
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="payment_account.disconnect",
        entity="payment_account",
        reason=reason,
        data={"provider": "asaas"},
    )
    return status(db, tenant_id)
