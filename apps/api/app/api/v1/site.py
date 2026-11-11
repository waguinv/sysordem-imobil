"""Fase 5a: site público — configuração no painel e API pública (sem login).

Os DTOs públicos (`Public*`) são listas FECHADAS de campos: nada de comprador, contrato, parcela,
reserva, corretor, proprietário, preço de lote indisponível ou descrição interna. Há teste que
compara as chaves do JSON público com o que é permitido.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import AuthContext, require
from app.auth.rbac import Perm
from app.core.db import DB_DEPENDS
from app.models import Development, Lot
from app.services import site as svc

router = APIRouter(tags=["site"])
public = APIRouter(prefix="/public/sites/{slug}", tags=["site público"])

Phone = Field(default=None, max_length=30)


# ─────────────────────────── painel ───────────────────────────


class SiteIn(BaseModel):
    published: bool = False
    title: str | None = Field(default=None, max_length=120)
    tagline: str | None = Field(default=None, max_length=200)
    about: str | None = Field(default=None, max_length=5000)
    contact_phone: str | None = Phone
    contact_whatsapp: str | None = Phone
    contact_email: EmailStr | None = None
    show_prices: bool = True
    lead_form_enabled: bool = True


class SiteOut(SiteIn):
    slug: str
    updated_at: datetime | None


class SiteDevOut(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    city: str | None
    state: str | None
    site_published: bool
    site_description: str | None
    site_published_at: datetime | None


class SiteDevIn(BaseModel):
    published: bool
    description: str | None = Field(default=None, max_length=5000)


def _clean(v: str | None) -> str | None:
    return v.strip() or None if v else None


def _site_out(ctx: AuthContext) -> SiteOut:
    s = svc.get_site(ctx.db, ctx.tenant_id)
    return SiteOut(
        slug=ctx.tenant.slug,
        published=s.published,
        title=s.title,
        tagline=s.tagline,
        about=s.about,
        contact_phone=s.contact_phone,
        contact_whatsapp=s.contact_whatsapp,
        contact_email=s.contact_email,
        show_prices=s.show_prices,
        lead_form_enabled=s.lead_form_enabled,
        updated_at=s.updated_at if s.updated_by else None,
    )


def _dev_out(d: Development) -> SiteDevOut:
    return SiteDevOut(
        id=d.id,
        name=d.name,
        status=d.status,
        city=d.city,
        state=d.state,
        site_published=d.site_published,
        site_description=d.site_description,
        site_published_at=d.site_published_at,
    )


@router.get("/site/settings", response_model=SiteOut)
def get_site(ctx: AuthContext = Depends(require(Perm.SITE_MANAGE))) -> SiteOut:
    return _site_out(ctx)


@router.put("/site/settings", response_model=SiteOut)
def save_site(body: SiteIn, ctx: AuthContext = Depends(require(Perm.SITE_MANAGE))) -> SiteOut:
    values = body.model_dump()
    for k in ("title", "tagline", "about", "contact_phone", "contact_whatsapp", "contact_email"):
        values[k] = _clean(values[k])
    svc.save_site(ctx.db, tenant_id=ctx.tenant_id, actor=ctx.user.id, values=values)
    return _site_out(ctx)


@router.get("/site/developments", response_model=list[SiteDevOut])
def site_developments(ctx: AuthContext = Depends(require(Perm.SITE_MANAGE))) -> list[SiteDevOut]:
    rows = ctx.db.scalars(
        select(Development).where(Development.tenant_id == ctx.tenant_id).order_by(Development.name)
    ).all()
    return [_dev_out(d) for d in rows]


@router.patch("/developments/{development_id}/site", response_model=SiteDevOut)
def publish_development(
    development_id: uuid.UUID, body: SiteDevIn, ctx: AuthContext = Depends(require(Perm.SITE_MANAGE))
) -> SiteDevOut:
    try:
        d = svc.publish_development(
            ctx.db,
            tenant_id=ctx.tenant_id,
            development_id=development_id,
            published=body.published,
            description=body.description,
            actor=ctx.user.id,
        )
    except svc.SiteError as e:
        raise _business(e) from None
    return _dev_out(d)


# ─────────────────────────── público ───────────────────────────


class PublicContact(BaseModel):
    phone: str | None
    whatsapp: str | None
    email: str | None


class PublicDevelopmentSummary(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    city: str | None
    state: str | None
    description: str
    lots_total: int
    lots_available: int
    price_from: Decimal | None


class PublicSite(BaseModel):
    slug: str
    name: str
    tagline: str | None
    about: str | None
    contact: PublicContact
    show_prices: bool
    lead_form_enabled: bool
    developments: list[PublicDevelopmentSummary]


class PublicLot(BaseModel):
    id: uuid.UUID
    number: str
    area_m2: Decimal
    front_m: Decimal | None
    available: bool
    price: Decimal | None  # só se a empresa exibe preços e o lote está disponível


class PublicBlock(BaseModel):
    code: str
    lots: list[PublicLot]


class PublicDevelopment(PublicDevelopmentSummary):
    blocks: list[PublicBlock]


class PublicLeadIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=30)
    message: str | None = Field(default=None, max_length=2000)
    development_id: uuid.UUID | None = None
    lot_id: uuid.UUID | None = None
    consent: Literal[True]  # LGPD: aceite explícito para ser contatado
    website: str | None = Field(default=None, max_length=200)  # campo-isca: humanos deixam vazio


class PublicLeadOut(BaseModel):
    status: Literal["recebido"] = "recebido"


def _business(e: svc.SiteError) -> HTTPException:
    if isinstance(e, svc.SiteNotFoundError):
        return HTTPException(status_code=404, detail=str(e))
    if isinstance(e, svc.TooManyRequestsError):
        return HTTPException(status_code=429, detail=str(e))
    return HTTPException(status_code=422, detail=str(e))


def _summary(s: svc.DevSummary, show_prices: bool) -> PublicDevelopmentSummary:
    d = s.dev
    return PublicDevelopmentSummary(
        id=d.id,
        name=d.name,
        kind=d.kind,
        city=d.city,
        state=d.state,
        description=d.site_description or "",
        lots_total=s.lots_total,
        lots_available=s.lots_available,
        price_from=s.price_from if show_prices else None,
    )


def _no_store(response: Response) -> None:
    # disponibilidade muda a cada venda; e nada de cache compartilhado entre empresas
    response.headers["Cache-Control"] = "no-store"


@public.get("", response_model=PublicSite)
def public_site(slug: str, response: Response, db: Session = DB_DEPENDS) -> PublicSite:
    try:
        tenant, s = svc.resolve(db, slug)
    except svc.SiteError as e:
        raise _business(e) from None
    _no_store(response)
    return PublicSite(
        slug=tenant.slug,
        name=s.title or tenant.name,
        tagline=s.tagline,
        about=s.about,
        contact=PublicContact(phone=s.contact_phone, whatsapp=s.contact_whatsapp, email=s.contact_email),
        show_prices=s.show_prices,
        lead_form_enabled=s.lead_form_enabled,
        developments=[_summary(x, s.show_prices) for x in svc.public_developments(db, tenant.id)],
    )


@public.get("/developments/{development_id}", response_model=PublicDevelopment)
def public_development(
    slug: str, development_id: uuid.UUID, response: Response, db: Session = DB_DEPENDS
) -> PublicDevelopment:
    try:
        tenant, s = svc.resolve(db, slug)
        d = svc.public_development(db, tenant.id, development_id)
    except svc.SiteError as e:
        raise _business(e) from None
    _no_store(response)
    blocks = svc.public_blocks(db, d.id)
    all_lots: list[Lot] = [lot for _, lots in blocks for lot in lots]
    available = [lot for lot in all_lots if lot.status == "disponivel"]
    summary = svc.DevSummary(
        d, len(all_lots), len(available), min((x.price for x in available), default=None)
    )

    def lot_out(lot: Lot) -> PublicLot:
        free = lot.status == "disponivel"
        return PublicLot(
            id=lot.id,
            number=lot.number,
            area_m2=lot.area_m2,
            front_m=lot.front_m,
            available=free,
            price=lot.price if (free and s.show_prices) else None,
        )

    return PublicDevelopment(
        **_summary(summary, s.show_prices).model_dump(),
        blocks=[PublicBlock(code=b.code, lots=[lot_out(x) for x in lots]) for b, lots in blocks],
    )


@public.post("/leads", response_model=PublicLeadOut, status_code=202)
def public_lead(slug: str, body: PublicLeadIn, db: Session = DB_DEPENDS) -> PublicLeadOut:
    try:
        tenant, s = svc.resolve(db, slug)
    except svc.SiteError as e:
        raise _business(e) from None
    if body.website:  # robô preencheu o campo-isca: finge que recebeu e não grava nada
        return PublicLeadOut()
    try:
        svc.submit_lead(
            db,
            tenant=tenant,
            site=s,
            data=svc.LeadInput(
                name=body.name,
                email=str(body.email) if body.email else None,
                phone=body.phone,
                message=body.message,
                development_id=body.development_id,
                lot_id=body.lot_id,
            ),
        )
    except svc.SiteError as e:
        raise _business(e) from None
    return PublicLeadOut()
