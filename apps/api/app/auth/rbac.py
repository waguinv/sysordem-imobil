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


_ALL = frozenset(Perm)

MATRIX: dict[Role, frozenset[Perm]] = {
    Role.OWNER: _ALL,
    Role.ADMIN: _ALL,
    Role.FINANCEIRO: frozenset({Perm.TENANT_VIEW, Perm.MEMBERS_VIEW, Perm.AUDIT_VIEW}),
    Role.COMERCIAL: frozenset({Perm.TENANT_VIEW, Perm.MEMBERS_VIEW}),
    Role.CORRETOR: frozenset({Perm.TENANT_VIEW}),
    Role.LEITURA: frozenset({Perm.TENANT_VIEW}),
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
