"""Escopo por corretor (Fase 2c — hipótese da decisão 15: "corretor vê só os próprios clientes").

Perfis com visão da empresa (owner, admin, comercial, financeiro) têm `broker_id=None`: sem filtro.
O perfil `corretor` fica restrito ao corretor ligado ao seu usuário (`brokers.user_id`):
- leads, propostas e reservas com `broker_id` igual ao dele;
- compradores dos quais ele é responsável (`customers.broker_id`) ou com quem tem lead,
  proposta ou reserva.
Registros fora do escopo respondem 404 (não revelam existência).
"""

import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import exists, or_, select

from app.auth.deps import AuthContext
from app.auth.rbac import Role
from app.models import Broker, Customer, Lead, Proposal, Reservation


@dataclass(frozen=True)
class Scope:
    broker_id: uuid.UUID | None  # None = toda a empresa

    @property
    def restricted(self) -> bool:
        return self.broker_id is not None


def scope_for(ctx: AuthContext) -> Scope:
    if ctx.role != Role.CORRETOR:
        return Scope(None)
    broker_id = ctx.db.scalar(
        select(Broker.id).where(
            Broker.tenant_id == ctx.tenant_id, Broker.user_id == ctx.user.id, Broker.is_active.is_(True)
        )
    )
    if broker_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seu usuário não está ligado a um corretor ativo. Peça ao administrador para vincular.",
        )
    return Scope(broker_id)


def customer_visible(scope: Scope) -> Any:
    """Condição SQL: comprador visível no escopo (use em `.where`)."""
    b = scope.broker_id
    return or_(
        Customer.broker_id == b,
        exists().where(Lead.customer_id == Customer.id, Lead.broker_id == b),
        exists().where(Proposal.customer_id == Customer.id, Proposal.broker_id == b),
        exists().where(Reservation.customer_id == Customer.id, Reservation.broker_id == b),
    )


def check_customer(ctx: AuthContext, scope: Scope, customer_id: uuid.UUID) -> None:
    """404 se o comprador não existe na empresa ou está fora do escopo do corretor."""
    stmt = select(Customer.id).where(Customer.id == customer_id, Customer.tenant_id == ctx.tenant_id)
    if scope.restricted:
        stmt = stmt.where(customer_visible(scope))
    if ctx.db.scalar(stmt) is None:
        raise HTTPException(status_code=404, detail="Comprador não encontrado")


def resolve_broker(scope: Scope, requested: uuid.UUID | None) -> uuid.UUID | None:
    """Corretor sempre atua em nome próprio; os demais escolhem (ou não) um corretor."""
    if not scope.restricted:
        return requested
    if requested is not None and requested != scope.broker_id:
        raise HTTPException(status_code=403, detail="Corretor só pode atuar em nome próprio")
    return scope.broker_id
