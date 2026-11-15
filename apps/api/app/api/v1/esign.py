"""Assinatura eletrônica de contratos (Documenso / simulador)."""

import hashlib
import logging
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import AuthContext, require
from app.auth.rbac import Perm
from app.core.config import get_settings
from app.core.db import DB_DEPENDS, set_tenant
from app.models import SalesContract, SignatureRequest, Tenant
from app.services import esign
from app.services import signatures as svc
from app.services.esign.base import InvalidSignatureError, ProviderError

router = APIRouter(tags=["assinatura eletrônica"])
log = logging.getLogger("imobil.esign")
MAX_WEBHOOK_BYTES = 256 * 1024


class ProviderOut(BaseModel):
    name: str
    label: str
    sandbox: bool


class SignerOut(BaseModel):
    name: str
    email: str
    role: str
    status: str
    signed_at: str | None = None


class SignatureRequestOut(BaseModel):
    id: uuid.UUID
    contract_id: uuid.UUID
    contract_version: int
    provider: str
    status: str
    document_sha256: str
    signers: list[SignerOut]
    detail: str | None
    created_at: datetime
    completed_at: datetime | None
    closed_at: datetime | None


class SendIn(BaseModel):
    expected_version: int = Field(ge=1)
    provider: str | None = None
    message: str | None = Field(default=None, max_length=1000)


class CancelIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class SimulateIn(BaseModel):
    action: Literal["sign_all", "reject"]
    reason: str | None = Field(default=None, max_length=300)


class SimulateOut(BaseModel):
    result: str
    detail: str | None
    request: SignatureRequestOut


def _out(r: SignatureRequest) -> SignatureRequestOut:
    return SignatureRequestOut(
        id=r.id,
        contract_id=r.contract_id,
        contract_version=r.contract_version,
        provider=r.provider,
        status=r.status,
        document_sha256=r.document_sha256,
        signers=[
            SignerOut(
                name=str(s.get("name", "")),
                email=str(s.get("email", "")),
                role=str(s.get("role", "")),
                status=str(s.get("status", "pendente")),
                signed_at=s.get("signed_at"),
            )
            for s in r.signers
        ],
        detail=r.detail,
        created_at=r.created_at,
        completed_at=r.completed_at,
        closed_at=r.closed_at,
    )


def _business(e: svc.SignatureError) -> HTTPException:
    return HTTPException(status_code=404 if isinstance(e, svc.SignatureNotFoundError) else 409, detail=str(e))


def _contract(ctx: AuthContext, contract_id: uuid.UUID) -> SalesContract:
    c = ctx.db.scalar(
        select(SalesContract).where(SalesContract.id == contract_id, SalesContract.tenant_id == ctx.tenant_id)
    )
    if c is None:
        raise HTTPException(status_code=404, detail="Contrato não encontrado")
    return c


@router.get("/esign/providers", response_model=list[ProviderOut])
def list_providers(ctx: AuthContext = Depends(require(Perm.CONTRACTS_VIEW))) -> list[ProviderOut]:
    return [ProviderOut(name=p.name, label=p.label, sandbox=p.name == "sandbox") for p in esign.available()]


@router.get("/contracts/{contract_id}/signature-requests", response_model=list[SignatureRequestOut])
def list_requests(
    contract_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.CONTRACTS_VIEW))
) -> list[SignatureRequestOut]:
    c = _contract(ctx, contract_id)
    rows = ctx.db.scalars(
        select(SignatureRequest)
        .where(SignatureRequest.contract_id == c.id, SignatureRequest.tenant_id == ctx.tenant_id)
        .order_by(SignatureRequest.created_at.desc())
    ).all()
    return [_out(r) for r in rows]


@router.get("/contracts/{contract_id}/document.pdf")
def contract_document(
    contract_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.CONTRACTS_VIEW))
) -> Response:
    """Prévia do quadro-resumo da versão vigente (o mesmo PDF que seria enviado para assinatura)."""
    c = _contract(ctx, contract_id)
    pdf, _ = svc.build_document(ctx.db, c, ctx.user.full_name)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="contrato-{c.number}-v{c.current_version}.pdf"',
            "Cache-Control": "no-store",
            "X-Content-SHA256": hashlib.sha256(pdf).hexdigest(),
        },
    )


# Enviar conclui a assinatura do contrato quando todos assinarem: exige a mesma permissão de assinar.
@router.post(
    "/contracts/{contract_id}/signature-requests", response_model=SignatureRequestOut, status_code=201
)
def send_for_signature(
    contract_id: uuid.UUID, body: SendIn, ctx: AuthContext = Depends(require(Perm.CONTRACTS_SIGN))
) -> SignatureRequestOut:
    provider = esign.get_provider(body.provider) if body.provider else esign.default_provider()
    if provider is None:
        raise HTTPException(status_code=409, detail="Assinatura eletrônica não configurada neste ambiente")
    try:
        req = svc.send(
            ctx.db,
            tenant_id=ctx.tenant_id,
            contract_id=contract_id,
            expected_version=body.expected_version,
            provider=provider,
            actor=ctx.user,
            message=body.message,
        )
    except svc.SignatureError as e:
        raise _business(e) from None
    except svc.contract_svc.ContractNotFoundError:
        raise HTTPException(status_code=404, detail="Contrato não encontrado") from None
    return _out(req)


@router.post("/signature-requests/{req_id}/cancel", response_model=SignatureRequestOut)
def cancel_request(
    req_id: uuid.UUID, body: CancelIn, ctx: AuthContext = Depends(require(Perm.CONTRACTS_SIGN))
) -> SignatureRequestOut:
    req = ctx.db.scalar(
        select(SignatureRequest).where(
            SignatureRequest.id == req_id, SignatureRequest.tenant_id == ctx.tenant_id
        )
    )
    if req is None:
        raise HTTPException(status_code=404, detail="Envio não encontrado")
    try:
        out = svc.cancel(
            ctx.db,
            tenant_id=ctx.tenant_id,
            req_id=req_id,
            provider=esign.get_provider(req.provider),
            actor=ctx.user.id,
            reason=body.reason,
        )
    except svc.SignatureError as e:
        raise _business(e) from None
    return _out(out)


@router.post("/signature-requests/{req_id}/sandbox/simulate", response_model=SimulateOut)
def simulate(
    req_id: uuid.UUID, body: SimulateIn, ctx: AuthContext = Depends(require(Perm.CONTRACTS_SIGN))
) -> SimulateOut:
    """Simulador: os signatários assinam (ou recusam) e o "provedor" envia o webhook assinado, que passa
    pela MESMA verificação e processamento de uma notificação real."""
    sbx = esign.get_provider("sandbox")
    if sbx is None:
        raise HTTPException(status_code=404, detail="Simulador desligado neste ambiente")
    req = ctx.db.scalar(
        select(SignatureRequest).where(
            SignatureRequest.id == req_id, SignatureRequest.tenant_id == ctx.tenant_id
        )
    )
    if req is None or req.provider != "sandbox" or not req.external_id:
        raise HTTPException(status_code=404, detail="Envio do simulador não encontrado")
    try:
        payload, headers = esign.sandbox().simulate(req.external_id, body.action, body.reason)
    except ProviderError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    sbx.verify_webhook(headers, payload)
    out = svc.process_webhook(ctx.db, tenant_id=ctx.tenant_id, req_id=req.id, provider=sbx, body=payload)
    ctx.db.refresh(req)
    return SimulateOut(result=out.result, detail=out.detail, request=_out(req))


# ─────────────────────────── webhook público (autenticado pelo provedor) ───────────────────────────


async def _raw_body(request: Request) -> bytes:
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BYTES:
        raise HTTPException(status_code=413, detail="Corpo muito grande")
    return body


def _parse_reference(ref: str | None) -> tuple[uuid.UUID, uuid.UUID] | None:
    if not ref or ":" not in ref:
        return None
    t, r = ref.split(":", 1)
    try:
        return uuid.UUID(t), uuid.UUID(r)
    except ValueError:
        return None


@router.post("/webhooks/esign/{provider}", include_in_schema=not get_settings().is_production)
def esign_webhook(
    provider: str, request: Request, body: bytes = Depends(_raw_body), db: Session = DB_DEPENDS
) -> dict[str, Any]:
    """Notificação do provedor. Sem token de usuário: a autenticação é o segredo/assinatura do provedor.

    A empresa vem da referência gravada no envelope (`<tenant>:<envio>`), só legível depois de autenticado;
    o envio é procurado SÓ dentro dela (RLS) e precisa casar com o id do envelope. Resposta 2xx =
    "recebido, não reenvie" — inclusive para duplicados e eventos sem efeito.
    """
    p = esign.get_provider(provider)
    if p is None:
        raise HTTPException(status_code=404, detail="Provedor desconhecido")
    try:
        p.verify_webhook(request.headers, body)
        ev = p.parse_webhook(body)
    except InvalidSignatureError as e:
        log.warning("webhook esign %s rejeitado: %s", provider, e)
        raise HTTPException(status_code=401, detail="Notificação não autenticada") from None
    ref = _parse_reference(ev.reference)
    if ref is None:
        return {"status": "ignorado"}  # documento de fora do sistema na mesma conta do provedor
    tenant_id, req_id = ref
    tenant = db.get(Tenant, tenant_id)
    if tenant is None or not tenant.is_active:
        return {"status": "ignorado"}
    set_tenant(db, tenant_id)
    out = svc.process_webhook(db, tenant_id=tenant_id, req_id=req_id, provider=p, body=body)
    return {"status": out.result}
