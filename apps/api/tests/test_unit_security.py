import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.auth.rbac import MATRIX, Perm, Role, can, can_assign
from app.core.config import get_settings
from app.core.security import create_access_token, decode_access_token, hash_password, verify_password


def test_password_hash_roundtrip():
    h = hash_password("uma-senha-forte")
    assert h.startswith("$argon2id$")
    assert verify_password("uma-senha-forte", h)
    assert not verify_password("errada", h)
    assert not verify_password("qualquer", None)


def test_token_roundtrip_and_claims():
    ids = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    tok = create_access_token(user_id=ids[0], tenant_id=ids[1], membership_id=ids[2])
    c = decode_access_token(tok)
    assert (c["sub"], c["tid"], c["mid"]) == tuple(map(str, ids))


def test_token_tampered_expired_or_unsigned_rejected():
    s = get_settings()
    base = {"iss": s.jwt_issuer, "sub": "x", "tid": "y", "mid": "z", "iat": datetime.now(UTC)}
    future = datetime.now(UTC) + timedelta(minutes=5)
    forged = jwt.encode(
        {**base, "exp": future}, "outro-segredo-qualquer-com-32-caracteres!", algorithm="HS256"
    )
    with pytest.raises(jwt.InvalidSignatureError):
        decode_access_token(forged)
    expired = jwt.encode(
        {**base, "exp": datetime.now(UTC) - timedelta(seconds=1)}, s.jwt_secret, algorithm="HS256"
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(expired)
    unsigned = jwt.encode({**base, "exp": future}, None, algorithm="none")
    with pytest.raises(jwt.InvalidAlgorithmError):
        decode_access_token(unsigned)


def test_rbac_matrix():
    assert all(role in MATRIX for role in Role)
    assert can("owner", Perm.MEMBERS_MANAGE)
    assert not can("leitura", Perm.MEMBERS_VIEW)
    assert not can("corretor", Perm.AUDIT_VIEW)
    assert not can("perfil-inexistente", Perm.TENANT_VIEW)


def test_only_owner_assigns_owner():
    assert can_assign("owner", "owner")
    assert not can_assign("admin", "owner")
    assert can_assign("admin", "comercial")
    assert not can_assign("financeiro", "leitura")
