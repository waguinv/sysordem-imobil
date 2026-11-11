"""RBAC: perfis por vínculo (membership) e matriz de permissões.

A matriz é a única fonte de verdade; rotas exigem permissões, nunca perfis.
"""

from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"  # dono da empresa (assinatura, tudo)
    ADMIN = "admin"
    FINANCEIRO = "financeiro"
    COMERCIAL = "comercial"
    CORRETOR = "corretor"
    LEITURA = "leitura"


class Perm(StrEnum):
    TENANT_VIEW = "tenant:view"
    MEMBERS_VIEW = "members:view"
    MEMBERS_MANAGE = "members:manage"
    AUDIT_VIEW = "audit:view"
    DEVELOPMENTS_VIEW = "developments:view"
    DEVELOPMENTS_MANAGE = "developments:manage"
    LOTS_STATUS = "lots:status"
    OWNERS_VIEW = "owners:view"
    OWNERS_MANAGE = "owners:manage"
    CUSTOMERS_VIEW = "customers:view"
    CUSTOMERS_MANAGE = "customers:manage"
    BROKERS_VIEW = "brokers:view"
    BROKERS_MANAGE = "brokers:manage"
    RESERVATIONS_VIEW = "reservations:view"
    RESERVATIONS_MANAGE = "reservations:manage"
    CONTRACTS_VIEW = "contracts:view"
    CONTRACTS_MANAGE = "contracts:manage"
    CONTRACTS_SIGN = "contracts:sign"
    SETTINGS_MANAGE = "settings:manage"
    LEADS_VIEW = "leads:view"
    LEADS_MANAGE = "leads:manage"
    PROPOSALS_VIEW = "proposals:view"
    PROPOSALS_MANAGE = "proposals:manage"
    FINANCE_VIEW = "finance:view"  # parcelas, contas a receber, recebimentos
    FINANCE_MANAGE = "finance:manage"  # registrar recebimentos
    FINANCE_REVERSE = "finance:reverse"  # estornar recebimentos
    SITE_MANAGE = "site:manage"  # Fase 5a: publicar site e empreendimentos (owner/admin)


_ALL = frozenset(Perm)

_BASE = frozenset({Perm.TENANT_VIEW, Perm.DEVELOPMENTS_VIEW})

MATRIX: dict[Role, frozenset[Perm]] = {
    Role.OWNER: _ALL,
    Role.ADMIN: _ALL,
    Role.FINANCEIRO: _BASE
    | {
        Perm.MEMBERS_VIEW,
        Perm.AUDIT_VIEW,
        Perm.OWNERS_VIEW,
        Perm.CUSTOMERS_VIEW,
        Perm.BROKERS_VIEW,
        Perm.RESERVATIONS_VIEW,
        Perm.CONTRACTS_VIEW,
        Perm.PROPOSALS_VIEW,
        Perm.FINANCE_VIEW,
        Perm.FINANCE_MANAGE,
        Perm.FINANCE_REVERSE,
    },
    Role.COMERCIAL: _BASE
    | {
        Perm.MEMBERS_VIEW,
        Perm.LOTS_STATUS,
        Perm.CUSTOMERS_VIEW,
        Perm.CUSTOMERS_MANAGE,
        Perm.BROKERS_VIEW,
        Perm.RESERVATIONS_VIEW,
        Perm.RESERVATIONS_MANAGE,
        Perm.CONTRACTS_VIEW,
        Perm.CONTRACTS_MANAGE,
        Perm.LEADS_VIEW,
        Perm.LEADS_MANAGE,
        Perm.PROPOSALS_VIEW,
        Perm.PROPOSALS_MANAGE,
    },
    # Corretor (Fase 2c): só os PRÓPRIOS leads, clientes, propostas e reservas — o filtro por corretor
    # é aplicado em app/auth/scope.py. Exige vínculo brokers.user_id; sem contratos nem cadastro
    # de corretores.
    Role.CORRETOR: _BASE
    | {
        Perm.CUSTOMERS_VIEW,
        Perm.CUSTOMERS_MANAGE,
        Perm.RESERVATIONS_VIEW,
        Perm.RESERVATIONS_MANAGE,
        Perm.LEADS_VIEW,
        Perm.LEADS_MANAGE,
        Perm.PROPOSALS_VIEW,
        Perm.PROPOSALS_MANAGE,
    },
    Role.LEITURA: _BASE,
}

ROLE_LABEL: dict[Role, str] = {
    Role.OWNER: "Proprietário",
    Role.ADMIN: "Administrador",
    Role.FINANCEIRO: "Financeiro",
    Role.COMERCIAL: "Comercial",
    Role.CORRETOR: "Corretor",
    Role.LEITURA: "Somente leitura",
}


def can(role: str, perm: Perm) -> bool:
    try:
        return perm in MATRIX[Role(role)]
    except ValueError:
        return False


def permissions_for(role: str) -> list[str]:
    try:
        return sorted(p.value for p in MATRIX[Role(role)])
    except ValueError:
        return []


def can_assign(actor_role: str, target_role: str) -> bool:
    """Só o proprietário cria/promove outro proprietário."""
    if target_role == Role.OWNER:
        return actor_role == Role.OWNER
    return can(actor_role, Perm.MEMBERS_MANAGE)
