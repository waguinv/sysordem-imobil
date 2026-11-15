"""Envio de contratos para assinatura eletrônica e processamento das notificações do provedor.

Regras:
- só contrato em rascunho, na versão que a pessoa está vendo (`expected_version`);
- um envio em andamento por contrato; enquanto ele existir, o contrato não muda de versão, não é
  cancelado nem assinado manualmente (cancele o envio antes);
- o webhook só AVISA: a conclusão é confirmada no provedor (`fetch_status`) antes de assinar o contrato,
  e só assina se a versão do contrato ainda for a do envio;
- travas na mesma ordem do resto do sistema: lote → contrato → envio.
"""

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.models import (
    Block,
    Broker,
    ContractVersion,
    Customer,
    Development,
    Lot,
    SalesContract,
    SignatureRequest,
    Tenant,
    User,
    WebhookEvent,
)
from app.services import audit
from app.services import contracts as contract_svc
from app.services.contract_pdf import ContractDoc, render
from app.services.esign.base import (
    EVENT_CANCELLED,
    EVENT_COMPLETED,
    EVENT_REJECTED,
    EVENT_SIGNED,
    STATUS_COMPLETED,
    STATUS_REJECTED,
    EnvelopeRequest,
    ProviderError,
    SignatureProvider,
    Signer,
    SignerState,
)

log = logging.getLogger("imobil.esign")
ACTIVE = ("enviando", "enviado")


class SignatureError(Exception):
    """Erro de regra de negócio (mensagem pronta para o usuário)."""


class SignatureNotFoundError(SignatureError):
    pass


_STORED_RESULT = {
    "assinado": "processado",
    "recusado": "processado",
    "cancelado": "processado",
    "parcial": "processado",
    "nao_confirmado": "ignorado",
    "ignorado": "ignorado",
}


@dataclass(frozen=True)
class WebhookOutcome:
    result: str
    detail: str | None = None


def webhook_provider_key(provider: SignatureProvider) -> str:
    return f"esign:{provider.name}"


def active_request(db: Session, tenant_id: uuid.UUID, contract_id: uuid.UUID) -> SignatureRequest | None:
    return db.scalar(
        select(SignatureRequest).where(
            SignatureRequest.tenant_id == tenant_id,
            SignatureRequest.contract_id == contract_id,
            SignatureRequest.status.in_(ACTIVE),
        )
    )


def build_document(db: Session, c: SalesContract, company_signer: str) -> tuple[bytes, int]:
    v = db.scalar(
        select(ContractVersion).where(
            ContractVersion.contract_id == c.id, ContractVersion.version == c.current_version
        )
    )
    customer = db.get(Customer, c.customer_id)
    lot = db.get(Lot, c.lot_id)
    tenant = db.get(Tenant, c.tenant_id)
    assert v and customer and lot and tenant
    block = db.get(Block, lot.block_id)
    dev = db.get(Development, lot.development_id)
    broker = db.get(Broker, c.broker_id) if c.broker_id else None
    assert block and dev
    return render(
        ContractDoc(
            company_name=tenant.name,
            number=c.number,
            version=c.current_version,
            customer_name=customer.name,
            customer_document=customer.document,
            broker_name=broker.name if broker else None,
            development_name=dev.name,
            block_code=block.code,
            lot_number=lot.number,
            area_m2=lot.area_m2,
            total_price=v.total_price,
            down_payment=v.down_payment,
            installments_count=v.installments_count,
            installment_amount=v.installment_amount,
            amortization=v.amortization,
            interest_monthly_pct=v.interest_monthly_pct,
            first_due_date=v.first_due_date,
            adjustment_index=v.adjustment_index,
            late_fee_pct=v.late_fee_pct,
            late_interest_monthly_pct=v.late_interest_monthly_pct,
            notes=v.notes,
            company_signer=company_signer,
            issued_on=business_today(),
        )
    )


def send(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    contract_id: uuid.UUID,
    expected_version: int,
    provider: SignatureProvider,
    actor: User,
    message: str | None,
) -> SignatureRequest:
    c, _lot = contract_svc.lock_contract(db, tenant_id, contract_id)
    if c.status != "rascunho":
        raise SignatureError(f"Só contrato em rascunho pode ser enviado (situação: {c.status})")
    if c.current_version != expected_version:
        raise SignatureError("As condições mudaram desde que você abriu o contrato. Revise antes de enviar.")
    if active_request(db, tenant_id, c.id):
        raise SignatureError("Já existe um envio para assinatura em andamento para este contrato")
    customer = db.get(Customer, c.customer_id)
    assert customer is not None
    if not customer.email:
        raise SignatureError("Cadastre o e-mail do comprador para enviar o contrato para assinatura")
    if customer.email.lower() == actor.email.lower():
        raise SignatureError("O representante da empresa e o comprador precisam ter e-mails diferentes")

    pdf, last_page = build_document(db, c, actor.full_name)
    signers = [
        Signer(name=customer.name, email=customer.email, role="comprador"),
        Signer(name=actor.full_name, email=actor.email, role="empresa"),
    ]
    req = SignatureRequest(
        tenant_id=tenant_id,
        contract_id=c.id,
        contract_version=c.current_version,
        provider=provider.name,
        status="enviando",
        document_sha256=hashlib.sha256(pdf).hexdigest(),
        signers=[{"name": s.name, "email": s.email, "role": s.role, "status": "pendente"} for s in signers],
        created_by=actor.id,
    )
    db.add(req)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        raise SignatureError("Já existe um envio para assinatura em andamento para este contrato") from None

    try:
        external_id = provider.create_envelope(
            EnvelopeRequest(
                reference=f"{tenant_id}:{req.id}",
                title=f"Contrato {c.number} (versão {c.current_version})",
                pdf=pdf,
                signers=signers,
                message=message or f"Contrato {c.number}: confira as condições e assine eletronicamente.",
                signature_page=last_page,
            )
        )
    except ProviderError as e:
        raise SignatureError(str(e)) from None
    req.external_id = external_id
    req.status = "enviado"
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor.id,
        action="contract.esign_send",
        entity="contract",
        entity_id=c.id,
        data={
            "signature_request_id": str(req.id),
            "provider": provider.name,
            "version": c.current_version,
            "sha256": req.document_sha256,
        },
    )
    return req


def _lock_request(
    db: Session, tenant_id: uuid.UUID, req_id: uuid.UUID
) -> tuple[SignatureRequest, SalesContract]:
    req = db.scalar(
        select(SignatureRequest).where(SignatureRequest.id == req_id, SignatureRequest.tenant_id == tenant_id)
    )
    if req is None:
        raise SignatureNotFoundError("Envio não encontrado")
    c, _lot = contract_svc.lock_contract(db, tenant_id, req.contract_id)
    db.refresh(req, with_for_update=True)
    return req, c


def _close(req: SignatureRequest, status: str, detail: str | None) -> None:
    now = datetime.now(UTC)
    req.status = status
    req.detail = detail
    req.closed_at = now
    if status == "assinado":
        req.completed_at = now


def _merge_signers(req: SignatureRequest, states: list[SignerState]) -> None:
    by_email = {s.email.lower(): s for s in states}
    merged = []
    for s in req.signers:
        st = by_email.get(str(s.get("email", "")).lower())
        merged.append({**s, "status": st.status, "signed_at": st.signed_at} if st else s)
    req.signers = merged


def cancel(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    req_id: uuid.UUID,
    provider: SignatureProvider | None,
    actor: uuid.UUID,
    reason: str,
) -> SignatureRequest:
    req, _c = _lock_request(db, tenant_id, req_id)
    if req.status not in ACTIVE:
        raise SignatureError(f"Envio não está em andamento (situação: {req.status})")
    if provider is None:
        raise SignatureError("Provedor de assinatura indisponível neste ambiente")
    if req.external_id:
        try:
            provider.cancel_envelope(req.external_id, reason)
        except ProviderError as e:
            raise SignatureError(f"{e}. Atualize a página: o documento pode já ter sido assinado.") from None
    _close(req, "cancelado", reason)
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="contract.esign_cancel",
        entity="contract",
        entity_id=req.contract_id,
        reason=reason,
        data={"signature_request_id": str(req.id)},
    )
    return req


def _complete(
    db: Session, req: SignatureRequest, c: SalesContract, provider: SignatureProvider
) -> WebhookOutcome:
    assert req.external_id
    try:
        st = provider.fetch_status(req.external_id)
    except ProviderError as e:
        return WebhookOutcome("nao_confirmado", str(e))  # o provedor reenvia; nada muda até confirmar
    if st.status != STATUS_COMPLETED:
        return WebhookOutcome("nao_confirmado", f"provedor informa situação {st.status}")
    _merge_signers(req, st.signers)
    if c.status != "rascunho" or c.current_version != req.contract_version:
        _close(req, "erro", "contrato mudou depois do envio; assinatura não aplicada")
        return WebhookOutcome("ignorado", req.detail)
    assert req.created_by
    contract_svc.sign(
        db,
        tenant_id=req.tenant_id,
        contract_id=c.id,
        expected_version=req.contract_version,
        actor=req.created_by,
        reason=f"assinatura eletrônica ({provider.label})",
        via_esign=True,
    )
    _close(req, "assinado", None)
    return WebhookOutcome("assinado", f"contrato {c.number} assinado")


def _reject(req: SignatureRequest, provider: SignatureProvider) -> WebhookOutcome:
    assert req.external_id
    try:
        st = provider.fetch_status(req.external_id)
    except ProviderError as e:
        return WebhookOutcome("nao_confirmado", str(e))
    if st.status != STATUS_REJECTED:
        return WebhookOutcome("nao_confirmado", f"provedor informa situação {st.status}")
    _merge_signers(req, st.signers)
    who = next((s for s in st.signers if s.status == "recusou"), None)
    _close(req, "recusado", (who.rejection_reason if who else None) or "assinatura recusada")
    return WebhookOutcome("recusado", req.detail)


def process_webhook(
    db: Session, *, tenant_id: uuid.UUID, req_id: uuid.UUID, provider: SignatureProvider, body: bytes
) -> WebhookOutcome:
    """Processa notificação JÁ AUTENTICADA no contexto do tenant."""
    ev = provider.parse_webhook(body)
    key = webhook_provider_key(provider)
    if db.scalar(
        select(WebhookEvent.id).where(
            WebhookEvent.tenant_id == tenant_id,
            WebhookEvent.provider == key,
            WebhookEvent.event_id == ev.event_id,
        )
    ):
        return WebhookOutcome("duplicado", "evento já recebido")

    req: SignatureRequest | None = None
    c: SalesContract | None = None
    try:
        req, c = _lock_request(db, tenant_id, req_id)
    except (SignatureNotFoundError, contract_svc.ContractError):
        req = None
    if req is not None and (req.provider != provider.name or req.external_id != ev.external_id):
        req = None  # referência não confere com o envelope: ignora

    we = WebhookEvent(
        tenant_id=tenant_id,
        provider=key,
        event_id=ev.event_id,
        event_type=ev.type,
        external_id=ev.external_id,
        payload=ev.raw,
        result="recebido",
    )
    try:
        with db.begin_nested():
            db.add(we)
            db.flush()
    except IntegrityError:
        return WebhookOutcome("duplicado", "evento já recebido")

    if req is None or c is None:
        out = WebhookOutcome("ignorado", "envio não encontrado")
    elif req.status not in ACTIVE:
        out = WebhookOutcome("ignorado", f"envio já encerrado ({req.status})")
    elif ev.type == EVENT_COMPLETED:
        out = _complete(db, req, c, provider)
    elif ev.type == EVENT_REJECTED:
        out = _reject(req, provider)
    elif ev.type == EVENT_CANCELLED:
        _close(req, "cancelado", "cancelado no provedor")
        out = WebhookOutcome("cancelado")
    elif ev.type == EVENT_SIGNED:
        _merge_signers(req, ev.signers)
        out = WebhookOutcome("parcial", "assinatura parcial registrada")
    else:
        out = WebhookOutcome("ignorado", "evento sem efeito")
    # webhook_events usa o vocabulário comum (processado/ignorado/...); o detalhe guarda o resto
    we.result = _STORED_RESULT.get(out.result, "ignorado")
    we.detail = f"{out.result}: {out.detail}" if out.detail else out.result
    we.processed_at = datetime.now(UTC)
    if req is not None:
        audit.record(
            db,
            tenant_id=tenant_id,
            actor_user_id=None,
            action=f"contract.esign_{out.result}",
            entity="contract",
            entity_id=req.contract_id,
            data={"signature_request_id": str(req.id), "event": ev.type},
        )
    db.flush()
    log.info("webhook esign %s %s → %s", provider.name, ev.type, out.result)
    return out
