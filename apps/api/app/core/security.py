"""Hash de senha (Argon2id) e tokens de acesso (JWT assinado)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings

_hasher = PasswordHasher()
# Usado quando o usuário não existe, para o tempo de resposta não revelar e-mails cadastrados.
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def create_access_token(*, user_id: uuid.UUID, tenant_id: uuid.UUID, membership_id: uuid.UUID) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "iss": s.jwt_issuer,
        "sub": str(user_id),
        "tid": str(tenant_id),
        "mid": str(membership_id),
        "iat": now,
        "exp": now + timedelta(minutes=s.access_token_minutes),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    s = get_settings()
    return jwt.decode(
        token,
        s.jwt_secret,
        algorithms=[s.jwt_algorithm],
        issuer=s.jwt_issuer,
        options={"require": ["exp", "iat", "sub", "tid", "mid", "iss"]},
    )


# ───────────────────────── Fase 5b: token do portal do comprador ─────────────────────────

PORTAL_AUDIENCE = "portal-comprador"


def create_portal_token(*, account_id: uuid.UUID, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> str:
    """Token do comprador: `aud` próprio e sem `mid` — não serve nas rotas da equipe (e vice-versa)."""
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "iss": s.jwt_issuer,
        "aud": PORTAL_AUDIENCE,
        "sub": str(account_id),
        "tid": str(tenant_id),
        "cid": str(customer_id),
        "iat": now,
        "exp": now + timedelta(minutes=s.portal_token_minutes),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_portal_token(token: str) -> dict[str, Any]:
    s = get_settings()
    return jwt.decode(
        token,
        s.jwt_secret,
        algorithms=[s.jwt_algorithm],
        issuer=s.jwt_issuer,
        audience=PORTAL_AUDIENCE,
        options={"require": ["exp", "iat", "sub", "tid", "cid", "iss", "aud"]},
    )
