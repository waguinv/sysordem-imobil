"""Site público (Fase 5a).

Regras:
- Só aparece o que foi PUBLICADO de propósito: o site da empresa (`site_settings.published`) e cada
  empreendimento (`developments.site_published`, com texto público próprio).
- O site nunca mostra compradores, CPF, contratos, parcelas, inadimplência, reservas ou corretores.
  Lote aparece só como "disponível" ou "indisponível" (reservado, vendido e bloqueado viram
  "indisponível"); preço só se a empresa quiser e só dos disponíveis.
- Formulário de contato: exige consentimento (LGPD, registrado em `leads.consent_at`), tem campo-isca
  contra robôs e limite por empresa e por contato. Contato repetido de quem já é lead em aberto vira
  nova anotação no mesmo lead (sem duplicar). A resposta é sempre a mesma — não revela se o contato
  já existia.
"""

import re
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from threading import Lock
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import set_tenant
from app.models import Block, Development, Lead, LeadActivity, Lot, SiteSettings, Tenant
from app.services import audit

OPEN_LEAD = ("novo", "em_contato", "qualificado")


class SiteError(Exception):
    """Erro de regra de negócio (mensagem pronta para o usuário)."""


class SiteNotFoundError(SiteError):
    pass


class TooManyRequestsError(SiteError):
    pass


# ─────────────────────────── configuração (painel) ───────────────────────────


def get_site(db: Session, tenant_id: uuid.UUID) -> SiteSettings:
    s = db.get(SiteSettings, tenant_id)
    return s or SiteSettings(tenant_id=tenant_id, published=False, show_prices=True, lead_form_enabled=True)


SITE_FIELDS = (
    "published",
    "title",
    "tagline",
    "about",
    "contact_phone",
    "contact_whatsapp",
    "contact_email",
    "show_prices",
    "lead_form_enabled",
)


def save_site(db: Session, *, tenant_id: uuid.UUID, actor: uuid.UUID, values: dict[str, Any]) -> SiteSettings:
    s = db.get(SiteSettings, tenant_id, with_for_update=True)
    before = {k: getattr(s, k) for k in SITE_FIELDS} if s else None
    if s is None:
        s = SiteSettings(tenant_id=tenant_id)
        db.add(s)
    for k in SITE_FIELDS:
        setattr(s, k, values[k])
    s.updated_by = actor
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="site.settings",
        entity="site",
        entity_id=tenant_id,
        data={"before": before, "after": {k: getattr(s, k) for k in SITE_FIELDS}},
    )
    return s


def publish_development(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    development_id: uuid.UUID,
    published: bool,
    description: str | None,
    actor: uuid.UUID,
) -> Development:
    d = db.scalar(
        select(Development)
        .where(Development.id == development_id, Development.tenant_id == tenant_id)
        .with_for_update()
    )
    if d is None:
        raise SiteNotFoundError("Empreendimento não encontrado")
    text = (description or "").strip() or None
    if published and not text:
        raise SiteError("Escreva o texto público do empreendimento antes de publicar")
    before = {"published": d.site_published, "description": d.site_description}
    d.site_description = text
    if published and not d.site_published:
        d.site_published_at = datetime.now(UTC)
    d.site_published = published
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="site.publish" if published else "site.unpublish",
        entity="development",
        entity_id=d.id,
        data={"before": before, "after": {"published": published, "description": text}},
    )
    return d


# ─────────────────────────── leitura pública ───────────────────────────


def resolve(db: Session, slug: str) -> tuple[Tenant, SiteSettings]:
    """Empresa ativa com site publicado; define o contexto RLS dela. Senão, 404 (sem distinção)."""
    tenant = db.scalar(select(Tenant).where(Tenant.slug == slug.lower()))
    if tenant is None or not tenant.is_active:
        raise SiteNotFoundError("Site não encontrado")
    set_tenant(db, tenant.id)
    s = db.get(SiteSettings, tenant.id)
    if s is None or not s.published:
        raise SiteNotFoundError("Site não encontrado")
    return tenant, s


@dataclass(frozen=True)
class DevSummary:
    dev: Development
    lots_total: int
    lots_available: int
    price_from: Decimal | None


def public_developments(db: Session, tenant_id: uuid.UUID) -> list[DevSummary]:
    avail = Lot.status == "disponivel"
    rows = db.execute(
        select(
            Development,
            func.count(Lot.id),
            func.count(Lot.id).filter(avail),
            func.min(Lot.price).filter(avail),
        )
        .outerjoin(Lot, Lot.development_id == Development.id)
        .where(Development.tenant_id == tenant_id, Development.site_published.is_(True))
        .group_by(Development.id)
        .order_by(Development.name)
    ).all()
    return [DevSummary(d, total, available, price_from) for d, total, available, price_from in rows]


def public_development(db: Session, tenant_id: uuid.UUID, development_id: uuid.UUID) -> Development:
    d = db.scalar(
        select(Development).where(
            Development.id == development_id,
            Development.tenant_id == tenant_id,
            Development.site_published.is_(True),
        )
    )
    if d is None:
        raise SiteNotFoundError("Empreendimento não encontrado")
    return d


def public_blocks(db: Session, development_id: uuid.UUID) -> list[tuple[Block, list[Lot]]]:
    blocks = db.scalars(
        select(Block)
        .where(Block.development_id == development_id)
        .order_by(func.length(Block.code), Block.code)
    ).all()
    lots = db.scalars(
        select(Lot).where(Lot.development_id == development_id).order_by(func.length(Lot.number), Lot.number)
    ).all()
    by_block: dict[uuid.UUID, list[Lot]] = defaultdict(list)
    for lot in lots:
        by_block[lot.block_id].append(lot)
    return [(b, by_block[b.id]) for b in blocks]


# ─────────────────────────── formulário de contato ───────────────────────────

_hits: dict[str, deque[float]] = defaultdict(deque)
_lock = Lock()
HOUR = 3600.0


def reset_throttle() -> None:
    with _lock:
        _hits.clear()


def _take(key: str, limit: int, now: float) -> bool:
    q = _hits[key]
    while q and now - q[0] > HOUR:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


def _digits(phone: str | None) -> str:
    return re.sub(r"\D", "", phone or "")


@dataclass(frozen=True)
class LeadInput:
    name: str
    email: str | None
    phone: str | None
    message: str | None
    development_id: uuid.UUID | None
    lot_id: uuid.UUID | None


def submit_lead(db: Session, *, tenant: Tenant, site: SiteSettings, data: LeadInput) -> None:
    if not site.lead_form_enabled:
        raise SiteNotFoundError("Formulário indisponível")
    email = (data.email or "").strip().lower() or None
    phone = (data.phone or "").strip() or None
    digits = _digits(phone)
    if not email and len(digits) < 10:
        raise SiteError("Informe um telefone com DDD ou um e-mail")

    dev = None
    lot = None
    if data.development_id:
        dev = public_development(db, tenant.id, data.development_id)
    if data.lot_id:
        if dev is None:
            raise SiteNotFoundError("Lote não encontrado")
        lot = db.scalar(select(Lot).where(Lot.id == data.lot_id, Lot.development_id == dev.id))
        if lot is None:
            raise SiteNotFoundError("Lote não encontrado")

    cfg = get_settings()
    now = time.monotonic()
    with _lock:
        contact = email or digits
        if not _take(f"t:{tenant.id}", cfg.public_leads_per_tenant_per_hour, now) or not _take(
            f"c:{tenant.id}:{contact}", cfg.public_leads_per_contact_per_hour, now
        ):
            raise TooManyRequestsError("Muitos envios. Tente de novo mais tarde ou fale por telefone.")

    interest = []
    if dev:
        interest.append(f"Empreendimento: {dev.name}")
    if lot:
        block = db.scalar(select(Block.code).where(Block.id == lot.block_id))
        interest.append(f"Quadra {block} · Lote {lot.number}")
    body = "\n".join(
        ["Contato pelo site."] + interest + ([f"Mensagem: {data.message.strip()}"] if data.message else [])
    )

    conds = []
    if email:
        conds.append(func.lower(Lead.email) == email)
    if len(digits) >= 10:
        conds.append(func.regexp_replace(Lead.phone, r"\D", "", "g") == digits)
    existing = db.scalar(
        select(Lead)
        .where(Lead.tenant_id == tenant.id, Lead.status.in_(OPEN_LEAD), or_(*conds))
        .order_by(Lead.created_at.desc())
        .limit(1)
    )
    consent = datetime.now(UTC)
    if existing is not None:
        lead = existing
        lead.consent_at = consent
        if dev and lead.development_id is None:
            lead.development_id = dev.id
    else:
        lead = Lead(
            tenant_id=tenant.id,
            name=data.name.strip(),
            email=email,
            phone=phone,
            source="site",
            development_id=dev.id if dev else None,
            status="novo",
            consent_at=consent,
        )
        db.add(lead)
        db.flush()
    db.add(
        LeadActivity(
            tenant_id=tenant.id,
            lead_id=lead.id,
            kind="nota",
            description=f"{body}\nConsentimento LGPD para contato registrado.",
        )
    )
    db.flush()
    audit.record(
        db,
        tenant_id=tenant.id,
        actor_user_id=None,
        action="site.lead",
        entity="lead",
        entity_id=lead.id,
        data={"new": existing is None},  # sem dados pessoais na auditoria
    )
