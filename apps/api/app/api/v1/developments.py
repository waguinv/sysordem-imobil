"""Empreendimentos, quadras, lotes e proprietários da área (Fase 1)."""

import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.schemas_fase1 import (
    BlockIn,
    BlockOut,
    DevelopmentIn,
    DevelopmentMapOut,
    DevelopmentOut,
    DevelopmentUpdate,
    LandOwnerIn,
    LandOwnerOut,
    LandOwnerUpdate,
    LotBatchIn,
    LotDetailOut,
    LotHistoryOut,
    LotIn,
    LotOut,
    LotStatusIn,
    LotUpdate,
    MapBlock,
    StatusCounts,
)
from app.auth.deps import AuthContext, require
from app.auth.rbac import Perm
from app.models import Block, Development, LandOwner, Lot, LotStatusHistory, User
from app.services import audit
from app.services import lots as lot_service
from app.services import reservations as reservation_service

router = APIRouter(tags=["empreendimentos"])


def _404(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} não encontrado")


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _jsonable(d: dict[str, Any]) -> dict[str, Any]:
    return {k: (str(v) if isinstance(v, Decimal | uuid.UUID) else v) for k, v in d.items()}


# Ordenação "natural" de códigos (2 antes de 10).
def _natural(col: Any) -> tuple[Any, Any]:
    return (func.length(col), col)


# ─────────────────────────── helpers de carga (sempre filtrando o tenant) ───────────────────────────


def _get_development(ctx: AuthContext, dev_id: uuid.UUID) -> Development:
    dev = ctx.db.scalar(
        select(Development).where(Development.id == dev_id, Development.tenant_id == ctx.tenant_id)
    )
    if dev is None:
        raise _404("Empreendimento")
    return dev


def _get_block(ctx: AuthContext, dev_id: uuid.UUID, block_id: uuid.UUID) -> Block:
    block = ctx.db.scalar(
        select(Block).where(
            Block.id == block_id, Block.development_id == dev_id, Block.tenant_id == ctx.tenant_id
        )
    )
    if block is None:
        raise _404("Quadra")
    return block


def _check_owner(ctx: AuthContext, owner_id: uuid.UUID | None) -> None:
    if owner_id is None:
        return
    exists = ctx.db.scalar(
        select(LandOwner.id).where(LandOwner.id == owner_id, LandOwner.tenant_id == ctx.tenant_id)
    )
    if exists is None:
        raise _404("Proprietário")


def _summaries(
    db: Session, tenant_id: uuid.UUID, dev_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, Any]]:
    if not dev_ids:
        return {}
    rows = db.execute(
        select(
            Lot.development_id,
            Lot.status,
            func.count(),
            func.coalesce(func.sum(case((Lot.status == "disponivel", Lot.price), else_=0)), 0),
        )
        .where(Lot.tenant_id == tenant_id, Lot.development_id.in_(dev_ids))
        .group_by(Lot.development_id, Lot.status)
    ).all()
    out: dict[uuid.UUID, dict[str, Any]] = {}
    for dev_id, st, n, available in rows:
        s = out.setdefault(dev_id, {"counts": {}, "total": 0, "available": Decimal("0.00")})
        s["counts"][st] = n
        s["total"] += n
        s["available"] += Decimal(available)
    return out


def _dev_out(dev: Development, summary: dict[str, Any] | None) -> DevelopmentOut:
    s = summary or {"counts": {}, "total": 0, "available": Decimal("0.00")}
    return DevelopmentOut(
        id=dev.id,
        name=dev.name,
        kind=dev.kind,
        status=dev.status,
        city=dev.city,
        state=dev.state,
        address=dev.address,
        description=dev.description,
        land_owner_id=dev.land_owner_id,
        created_at=dev.created_at,
        lots_total=s["total"],
        counts=StatusCounts(**s["counts"]),
        available_value=s["available"].quantize(Decimal("0.01")),
    )


# ─────────────────────────── proprietários da área ───────────────────────────


@router.get("/land-owners", response_model=list[LandOwnerOut])
def list_land_owners(ctx: AuthContext = Depends(require(Perm.OWNERS_VIEW))) -> list[LandOwner]:
    return list(
        ctx.db.scalars(
            select(LandOwner).where(LandOwner.tenant_id == ctx.tenant_id).order_by(LandOwner.name)
        ).all()
    )


@router.post("/land-owners", response_model=LandOwnerOut, status_code=status.HTTP_201_CREATED)
def create_land_owner(
    body: LandOwnerIn, ctx: AuthContext = Depends(require(Perm.OWNERS_MANAGE))
) -> LandOwner:
    owner = LandOwner(tenant_id=ctx.tenant_id, **body.model_dump())
    ctx.db.add(owner)
    ctx.db.flush()
    ctx.db.refresh(owner)
    # Dados pessoais não vão para a auditoria; só o nome.
    audit.record(
        ctx.db,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        action="land_owner.create",
        entity="land_owner",
        entity_id=owner.id,
        data={"name": owner.name},
    )
    return owner


@router.patch("/land-owners/{owner_id}", response_model=LandOwnerOut)
def update_land_owner(
    owner_id: uuid.UUID, body: LandOwnerUpdate, ctx: AuthContext = Depends(require(Perm.OWNERS_MANAGE))
) -> LandOwner:
    owner = ctx.db.scalar(
        select(LandOwner).where(LandOwner.id == owner_id, LandOwner.tenant_id == ctx.tenant_id)
    )
    if owner is None:
        raise _404("Proprietário")
    changes = body.model_dump(exclude_unset=True, exclude={"reason"})
    for k, v in changes.items():
        setattr(owner, k, v)
    ctx.db.flush()
    audit.record(
        ctx.db,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        action="land_owner.update",
        entity="land_owner",
        entity_id=owner.id,
        reason=body.reason,
        data={"fields": sorted(changes)},
    )
    return owner


# ─────────────────────────── empreendimentos ───────────────────────────


@router.get("/developments", response_model=list[DevelopmentOut])
def list_developments(ctx: AuthContext = Depends(require(Perm.DEVELOPMENTS_VIEW))) -> list[DevelopmentOut]:
    devs = ctx.db.scalars(
        select(Development).where(Development.tenant_id == ctx.tenant_id).order_by(Development.name)
    ).all()
    sums = _summaries(ctx.db, ctx.tenant_id, [d.id for d in devs])
    return [_dev_out(d, sums.get(d.id)) for d in devs]


@router.post("/developments", response_model=DevelopmentOut, status_code=status.HTTP_201_CREATED)
def create_development(
    body: DevelopmentIn, ctx: AuthContext = Depends(require(Perm.DEVELOPMENTS_MANAGE))
) -> DevelopmentOut:
    _check_owner(ctx, body.land_owner_id)
    dev = Development(tenant_id=ctx.tenant_id, **body.model_dump())
    ctx.db.add(dev)
    ctx.db.flush()
    ctx.db.refresh(dev)
    audit.record(
        ctx.db,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        action="development.create",
        entity="development",
        entity_id=dev.id,
        data={"name": dev.name},
    )
    return _dev_out(dev, None)


@router.get("/developments/{dev_id}", response_model=DevelopmentOut)
def get_development(
    dev_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.DEVELOPMENTS_VIEW))
) -> DevelopmentOut:
    dev = _get_development(ctx, dev_id)
    return _dev_out(dev, _summaries(ctx.db, ctx.tenant_id, [dev.id]).get(dev.id))


@router.patch("/developments/{dev_id}", response_model=DevelopmentOut)
def update_development(
    dev_id: uuid.UUID, body: DevelopmentUpdate, ctx: AuthContext = Depends(require(Perm.DEVELOPMENTS_MANAGE))
) -> DevelopmentOut:
    dev = _get_development(ctx, dev_id)
    changes = body.model_dump(exclude_unset=True, exclude={"reason"})
    if "land_owner_id" in changes:
        _check_owner(ctx, changes["land_owner_id"])
    before = {k: getattr(dev, k) for k in changes}
    for k, v in changes.items():
        setattr(dev, k, v.upper() if k == "state" and v else v)
    ctx.db.flush()
    audit.record(
        ctx.db,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        action="development.update",
        entity="development",
        entity_id=dev.id,
        reason=body.reason,
        data={"before": _jsonable(before), "after": _jsonable(changes)},
    )
    return _dev_out(dev, _summaries(ctx.db, ctx.tenant_id, [dev.id]).get(dev.id))


@router.get("/developments/{dev_id}/map", response_model=DevelopmentMapOut)
def development_map(
    dev_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.DEVELOPMENTS_VIEW))
) -> DevelopmentMapOut:
    dev = _get_development(ctx, dev_id)
    reservation_service.expire_due(ctx.db, ctx.tenant_id)  # mapa sempre reflete reservas vencidas
    blocks = ctx.db.scalars(
        select(Block)
        .where(Block.development_id == dev.id, Block.tenant_id == ctx.tenant_id)
        .order_by(*_natural(Block.code))
    ).all()
    lots = ctx.db.scalars(
        select(Lot)
        .where(Lot.development_id == dev.id, Lot.tenant_id == ctx.tenant_id)
        .order_by(*_natural(Lot.number))
    ).all()
    by_block: dict[uuid.UUID, list[LotOut]] = {b.id: [] for b in blocks}
    for lot in lots:
        by_block.setdefault(lot.block_id, []).append(LotOut.model_validate(lot))
    return DevelopmentMapOut(
        development=_dev_out(dev, _summaries(ctx.db, ctx.tenant_id, [dev.id]).get(dev.id)),
        blocks=[MapBlock(id=b.id, code=b.code, lots=by_block[b.id]) for b in blocks],
    )


# ─────────────────────────── quadras ───────────────────────────


@router.post("/developments/{dev_id}/blocks", response_model=BlockOut, status_code=status.HTTP_201_CREATED)
def create_block(
    dev_id: uuid.UUID, body: BlockIn, ctx: AuthContext = Depends(require(Perm.DEVELOPMENTS_MANAGE))
) -> Block:
    dev = _get_development(ctx, dev_id)
    code = body.code.upper()
    if ctx.db.scalar(select(Block.id).where(Block.development_id == dev.id, Block.code == code)):
        raise _conflict("Já existe uma quadra com este código")
    block = Block(tenant_id=ctx.tenant_id, development_id=dev.id, code=code)
    ctx.db.add(block)
    ctx.db.flush()
    audit.record(
        ctx.db,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        action="block.create",
        entity="block",
        entity_id=block.id,
        data={"development_id": str(dev.id), "code": code},
    )
    return block


@router.delete("/developments/{dev_id}/blocks/{block_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_block(
    dev_id: uuid.UUID, block_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.DEVELOPMENTS_MANAGE))
) -> None:
    block = _get_block(ctx, dev_id, block_id)
    if ctx.db.scalar(select(func.count()).select_from(Lot).where(Lot.block_id == block.id)):
        raise _conflict("Quadra com lotes não pode ser excluída")
    ctx.db.delete(block)
    audit.record(
        ctx.db,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        action="block.delete",
        entity="block",
        entity_id=block.id,
        data={"code": block.code},
    )


# ─────────────────────────── lotes ───────────────────────────


def _new_lot(ctx: AuthContext, dev: Development, block: Block, number: str, **fields: Any) -> Lot:
    lot = Lot(
        tenant_id=ctx.tenant_id,
        development_id=dev.id,
        block_id=block.id,
        number=number,
        status=lot_service.LotStatus.DISPONIVEL.value,
        version=1,
        **fields,
    )
    ctx.db.add(lot)
    ctx.db.flush()
    ctx.db.add(
        LotStatusHistory(
            tenant_id=ctx.tenant_id,
            lot_id=lot.id,
            from_status=None,
            to_status=lot.status,
            actor_user_id=ctx.user.id,
            reason="cadastro",
        )
    )
    return lot


@router.post("/developments/{dev_id}/lots", response_model=LotOut, status_code=status.HTTP_201_CREATED)
def create_lot(
    dev_id: uuid.UUID, body: LotIn, ctx: AuthContext = Depends(require(Perm.DEVELOPMENTS_MANAGE))
) -> Lot:
    dev = _get_development(ctx, dev_id)
    block = _get_block(ctx, dev.id, body.block_id)
    number = body.number.upper()
    if ctx.db.scalar(select(Lot.id).where(Lot.block_id == block.id, Lot.number == number)):
        raise _conflict("Já existe um lote com este número na quadra")
    lot = _new_lot(
        ctx,
        dev,
        block,
        number,
        area_m2=body.area_m2,
        front_m=body.front_m,
        price=body.price,
        notes=body.notes,
    )
    ctx.db.flush()
    audit.record(
        ctx.db,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        action="lot.create",
        entity="lot",
        entity_id=lot.id,
        data=_jsonable({"block": block.code, "number": number, "price": body.price}),
    )
    return lot


@router.post(
    "/developments/{dev_id}/lots/batch", response_model=list[LotOut], status_code=status.HTTP_201_CREATED
)
def create_lots_batch(
    dev_id: uuid.UUID, body: LotBatchIn, ctx: AuthContext = Depends(require(Perm.DEVELOPMENTS_MANAGE))
) -> list[Lot]:
    dev = _get_development(ctx, dev_id)
    block = _get_block(ctx, dev.id, body.block_id)
    numbers = [str(n).zfill(body.pad) for n in range(body.first, body.last + 1)]
    taken = set(ctx.db.scalars(select(Lot.number).where(Lot.block_id == block.id, Lot.number.in_(numbers))))
    if taken:
        raise _conflict(f"Números já existentes na quadra: {', '.join(sorted(taken))}")
    try:
        lots = [
            _new_lot(ctx, dev, block, n, area_m2=body.area_m2, front_m=body.front_m, price=body.price)
            for n in numbers
        ]
        ctx.db.flush()
    except IntegrityError:
        raise _conflict("Conflito ao criar lotes; tente novamente") from None
    audit.record(
        ctx.db,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        action="lot.batch_create",
        entity="block",
        entity_id=block.id,
        data=_jsonable(
            {"first": numbers[0], "last": numbers[-1], "count": len(numbers), "price": body.price}
        ),
    )
    return lots


def _get_lot(ctx: AuthContext, lot_id: uuid.UUID) -> Lot:
    lot = ctx.db.scalar(select(Lot).where(Lot.id == lot_id, Lot.tenant_id == ctx.tenant_id))
    if lot is None:
        raise _404("Lote")
    return lot


def _detail(ctx: AuthContext, lot: Lot) -> LotDetailOut:
    block_code, dev_name = ctx.db.execute(
        select(Block.code, Development.name)
        .join(Development, Development.id == Block.development_id)
        .where(Block.id == lot.block_id)
    ).one()
    from app.models import Reservation, SalesContract

    active = ctx.db.scalar(
        select(Reservation.id).where(
            Reservation.tenant_id == ctx.tenant_id,
            Reservation.lot_id == lot.id,
            Reservation.status == "ativa",
        )
    )
    contract = ctx.db.execute(
        select(SalesContract.id, SalesContract.status).where(
            SalesContract.tenant_id == ctx.tenant_id,
            SalesContract.lot_id == lot.id,
            SalesContract.status != "cancelado",
        )
    ).one_or_none()
    held = active is not None or (contract is not None and contract.status == "rascunho")
    return LotDetailOut(
        **LotOut.model_validate(lot).model_dump(),
        block_code=block_code,
        development_name=dev_name,
        allowed_transitions=lot_service.manual_transitions(lot.status, held),
        active_reservation_id=active,
        contract_id=contract.id if contract else None,
    )


@router.get("/lots/{lot_id}", response_model=LotDetailOut)
def get_lot(lot_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.DEVELOPMENTS_VIEW))) -> LotDetailOut:
    lot = _get_lot(ctx, lot_id)
    if reservation_service.expire_due(ctx.db, ctx.tenant_id, lot_id=lot.id):
        ctx.db.refresh(lot)
    return _detail(ctx, lot)


@router.patch("/lots/{lot_id}", response_model=LotDetailOut)
def update_lot(
    lot_id: uuid.UUID, body: LotUpdate, ctx: AuthContext = Depends(require(Perm.DEVELOPMENTS_MANAGE))
) -> LotDetailOut:
    lot = ctx.db.scalar(select(Lot).where(Lot.id == lot_id, Lot.tenant_id == ctx.tenant_id).with_for_update())
    if lot is None:
        raise _404("Lote")
    if lot.version != body.expected_version:
        raise _conflict("O lote foi alterado por outra pessoa. Recarregue a página.")
    if lot.status == lot_service.LotStatus.VENDIDO and body.price is not None and body.price != lot.price:
        raise _conflict("Preço de lote vendido não pode ser alterado")
    changes = body.model_dump(exclude_unset=True, exclude={"reason", "expected_version"})
    before = {k: getattr(lot, k) for k in changes}
    for k, v in changes.items():
        setattr(lot, k, v)
    lot.version += 1
    ctx.db.flush()
    audit.record(
        ctx.db,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        action="lot.update",
        entity="lot",
        entity_id=lot.id,
        reason=body.reason,
        data={"before": _jsonable(before), "after": _jsonable(changes)},
    )
    return _detail(ctx, lot)


@router.post("/lots/{lot_id}/status", response_model=LotDetailOut)
def change_lot_status(
    lot_id: uuid.UUID, body: LotStatusIn, ctx: AuthContext = Depends(require(Perm.LOTS_STATUS))
) -> LotDetailOut:
    try:
        lot = lot_service.change_status(
            ctx.db,
            tenant_id=ctx.tenant_id,
            lot_id=lot_id,
            to_status=body.to_status,
            expected_version=body.expected_version,
            actor_user_id=ctx.user.id,
            reason=body.reason,
        )
    except lot_service.LotNotFoundError:
        raise _404("Lote") from None
    except lot_service.LotConflictError:
        raise _conflict("O lote foi alterado por outra pessoa. Recarregue a página.") from None
    except lot_service.InvalidTransitionError as e:
        raise HTTPException(status_code=422, detail=f"Mudança de status não permitida: {e}") from None
    return _detail(ctx, lot)


@router.get("/lots/{lot_id}/history", response_model=list[LotHistoryOut])
def lot_history(
    lot_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.DEVELOPMENTS_VIEW))
) -> list[LotHistoryOut]:
    lot = _get_lot(ctx, lot_id)
    rows = ctx.db.execute(
        select(LotStatusHistory, User.full_name)
        .outerjoin(User, User.id == LotStatusHistory.actor_user_id)
        .where(LotStatusHistory.lot_id == lot.id, LotStatusHistory.tenant_id == ctx.tenant_id)
        .order_by(LotStatusHistory.created_at.desc(), LotStatusHistory.id)
    ).all()
    return [LotHistoryOut.model_validate(h).model_copy(update={"actor_name": name}) for h, name in rows]
