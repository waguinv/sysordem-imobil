"""DTOs da Fase 1. Dinheiro e área são Decimal (serializados como string no JSON)."""

import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.services.lots import LotStatus

Money = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]
Area = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
Front = Annotated[Decimal, Field(gt=0, max_digits=8, decimal_places=2)]
Kind = Literal["loteamento", "chacaras", "condominio"]
DevStatus = Literal["planejamento", "lancamento", "em_vendas", "encerrado"]
Code = Annotated[str, Field(min_length=1, max_length=20, pattern=r"^[0-9A-Za-z][0-9A-Za-z\-]*$")]


# ── Proprietários da área ──


class LandOwnerIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    document: str | None = Field(default=None, max_length=20)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=30)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("document")
    @classmethod
    def _digits(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        d = re.sub(r"\D", "", v)
        if len(d) not in (11, 14):
            raise ValueError("CPF (11 dígitos) ou CNPJ (14 dígitos)")
        return d


class LandOwnerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=30)
    notes: str | None = Field(default=None, max_length=2000)
    reason: str = Field(min_length=3, max_length=500)


class LandOwnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    document: str | None
    email: str | None
    phone: str | None
    notes: str | None
    created_at: datetime


# ── Empreendimentos ──


class DevelopmentIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    kind: Kind = "loteamento"
    status: DevStatus = "planejamento"
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, min_length=2, max_length=2)
    address: str | None = Field(default=None, max_length=240)
    description: str | None = Field(default=None, max_length=4000)
    land_owner_id: uuid.UUID | None = None

    @field_validator("state")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.upper() if v else None


class DevelopmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    kind: Kind | None = None
    status: DevStatus | None = None
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, min_length=2, max_length=2)
    address: str | None = Field(default=None, max_length=240)
    description: str | None = Field(default=None, max_length=4000)
    land_owner_id: uuid.UUID | None = None
    reason: str = Field(min_length=3, max_length=500)


class StatusCounts(BaseModel):
    disponivel: int = 0
    reservado: int = 0
    vendido: int = 0
    bloqueado: int = 0


class DevelopmentOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    status: str
    city: str | None
    state: str | None
    address: str | None
    description: str | None
    land_owner_id: uuid.UUID | None
    created_at: datetime
    lots_total: int = 0
    counts: StatusCounts = StatusCounts()
    available_value: Decimal = Decimal("0.00")


# ── Quadras e lotes ──


class BlockIn(BaseModel):
    code: Code


class BlockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str


class LotIn(BaseModel):
    block_id: uuid.UUID
    number: Code
    area_m2: Area
    front_m: Front | None = None
    price: Money
    notes: str | None = Field(default=None, max_length=2000)


class LotBatchIn(BaseModel):
    """Cria lotes numerados de `first` a `last` na quadra, com a mesma área e preço."""

    block_id: uuid.UUID
    first: int = Field(ge=1, le=9999)
    last: int = Field(ge=1, le=9999)
    area_m2: Area
    front_m: Front | None = None
    price: Money
    pad: int = Field(default=2, ge=1, le=4)

    @model_validator(mode="after")
    def _range(self) -> "LotBatchIn":
        if self.last < self.first:
            raise ValueError("last deve ser ≥ first")
        if self.last - self.first >= 500:
            raise ValueError("no máximo 500 lotes por vez")
        return self


class LotUpdate(BaseModel):
    area_m2: Area | None = None
    front_m: Front | None = None
    price: Money | None = None
    notes: str | None = Field(default=None, max_length=2000)
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=500)


class LotStatusIn(BaseModel):
    to_status: LotStatus
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=500)


class LotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    development_id: uuid.UUID
    block_id: uuid.UUID
    number: str
    area_m2: Decimal
    front_m: Decimal | None
    price: Decimal
    status: str
    version: int
    notes: str | None


class LotDetailOut(LotOut):
    block_code: str
    development_name: str
    allowed_transitions: list[str]


class MapBlock(BaseModel):
    id: uuid.UUID
    code: str
    lots: list[LotOut]


class DevelopmentMapOut(BaseModel):
    development: DevelopmentOut
    blocks: list[MapBlock]


class LotHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    from_status: str | None
    to_status: str
    actor_user_id: uuid.UUID | None
    actor_name: str | None = None
    reason: str | None
    created_at: datetime
