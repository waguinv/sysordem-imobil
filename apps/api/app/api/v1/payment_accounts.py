"""Configurações → Recebimentos: conta Asaas da empresa e CNPJ da empresa."""

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.auth.deps import AuthContext, require
from app.auth.rbac import Perm
from app.services import audit
from app.services import payment_accounts as svc

router = APIRouter(tags=["recebimentos"])


class PaymentAccountOut(BaseModel):
    configured: bool
    credentials_available: bool
    environments: list[str]
    provider: str | None
    environment: str | None
    account_name: str | None
    api_key_hint: str | None
    webhook: bool
    webhook_url: str | None
    pending_charges: int
    company_document: str | None


class PaymentAccountIn(BaseModel):
    api_key: str = Field(min_length=20, max_length=400)
    environment: Literal["sandbox", "production"]


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class CompanyDocumentIn(BaseModel):
    document: str = Field(max_length=20)

    @field_validator("document")
    @classmethod
    def _digits(cls, v: str) -> str:
        d = re.sub(r"\D", "", v)
        if len(d) not in (11, 14):
            raise ValueError("CNPJ (14 dígitos) ou CPF (11 dígitos)")
        return d


def _out(ctx: AuthContext, s: svc.AccountStatus) -> PaymentAccountOut:
    return PaymentAccountOut(**s.__dict__, company_document=ctx.tenant.document)


@router.get("/settings/payments", response_model=PaymentAccountOut)
def get_payment_account(ctx: AuthContext = Depends(require(Perm.SETTINGS_MANAGE))) -> PaymentAccountOut:
    return _out(ctx, svc.status(ctx.db, ctx.tenant_id))


@router.put("/settings/payments", response_model=PaymentAccountOut)
def connect_payment_account(
    body: PaymentAccountIn, ctx: AuthContext = Depends(require(Perm.SETTINGS_MANAGE))
) -> PaymentAccountOut:
    try:
        s = svc.connect(
            ctx.db,
            tenant_id=ctx.tenant_id,
            api_key=body.api_key,
            environment=body.environment,
            actor=ctx.user.id,
            actor_email=ctx.user.email,
        )
    except svc.PaymentAccountError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return _out(ctx, s)


@router.post("/settings/payments/disconnect", response_model=PaymentAccountOut)
def disconnect_payment_account(
    body: ReasonIn, ctx: AuthContext = Depends(require(Perm.SETTINGS_MANAGE))
) -> PaymentAccountOut:
    try:
        s = svc.disconnect(ctx.db, tenant_id=ctx.tenant_id, actor=ctx.user.id, reason=body.reason)
    except svc.PaymentAccountError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return _out(ctx, s)


@router.put("/settings/company-document", response_model=PaymentAccountOut)
def set_company_document(
    body: CompanyDocumentIn, ctx: AuthContext = Depends(require(Perm.SETTINGS_MANAGE, allow_suspended=True))
) -> PaymentAccountOut:
    """CNPJ da empresa: o Asaas da plataforma exige para emitir a fatura da assinatura (por isso vale
    também com a assinatura suspensa — é o que permite pagar e voltar)."""
    before = ctx.tenant.document
    ctx.tenant.document = body.document
    ctx.db.flush()
    audit.record(
        ctx.db,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        action="tenant.document",
        entity="tenant",
        entity_id=ctx.tenant_id,
        data={"before": before, "after": body.document},
    )
    return _out(ctx, svc.status(ctx.db, ctx.tenant_id))
