"""Verificação em duas etapas (TOTP + códigos de recuperação).

- O segredo TOTP é cifrado com o cofre de credenciais (`app.core.credentials`, finalidade "mfa-totp",
  amarrado ao id do usuário). Sem a chave do cofre no servidor, a ativação fica indisponível.
- Ativar exige a SENHA atual (uma sessão roubada não consegue ativar um MFA do atacante e trancar o dono
  fora) e um código do aplicativo (prova que o celular foi configurado).
- Desligar e gerar novos códigos de recuperação exigem senha + código (TOTP ou recuperação).
- Cada código TOTP vale uma vez (`last_step`); códigos de recuperação são de uso único (Argon2id).
- O MFA é do usuário e vale em todas as empresas dele. Por isso NÃO há "desligar o MFA de outro membro":
  o administrador de uma empresa poderia tirar a proteção do dono de outra (decisão pendente 42).
"""

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import credentials, totp
from app.core.security import hash_password, verify_password
from app.models import User, UserMfa, UserMfaRecoveryCode

PURPOSE = "mfa-totp"
ISSUER = "SysOrdem Imobil"
RECOVERY_CODES = 10
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # sem 0/o, 1/l/i


class MfaError(Exception):
    """Erro de regra (mensagem pronta para o usuário)."""


class MfaUnavailableError(MfaError):
    pass


@dataclass(frozen=True)
class Status:
    enabled: bool
    available: bool
    recovery_codes_left: int
    enabled_at: datetime | None


def _row(db: Session, user_id: uuid.UUID, *, lock: bool = False) -> UserMfa | None:
    stmt = select(UserMfa).where(UserMfa.user_id == user_id)
    return db.scalar(stmt.with_for_update() if lock else stmt)


def is_enabled(db: Session, user_id: uuid.UUID) -> bool:
    row = _row(db, user_id)
    return row is not None and row.enabled_at is not None


def enabled_users(db: Session, user_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not user_ids:
        return set()
    return set(
        db.scalars(
            select(UserMfa.user_id).where(UserMfa.user_id.in_(user_ids), UserMfa.enabled_at.is_not(None))
        ).all()
    )


def _codes_left(db: Session, user_id: uuid.UUID) -> int:
    return len(
        db.scalars(
            select(UserMfaRecoveryCode.id).where(
                UserMfaRecoveryCode.user_id == user_id, UserMfaRecoveryCode.used_at.is_(None)
            )
        ).all()
    )


def status(db: Session, user_id: uuid.UUID) -> Status:
    row = _row(db, user_id)
    enabled = row is not None and row.enabled_at is not None
    return Status(
        enabled=enabled,
        available=credentials.available(),
        recovery_codes_left=_codes_left(db, user_id) if enabled else 0,
        enabled_at=row.enabled_at if row else None,
    )


def _seal(secret: str, user_id: uuid.UUID) -> str:
    try:
        return credentials.seal(secret, tenant_id=user_id, purpose=PURPOSE)
    except credentials.CredentialsUnavailableError:
        raise MfaUnavailableError(
            "Verificação em duas etapas indisponível neste servidor — fale com o suporte"
        ) from None


def _open(sealed: str, user_id: uuid.UUID) -> str:
    try:
        return credentials.open_sealed(sealed, tenant_id=user_id, purpose=PURPOSE)
    except credentials.CredentialsUnavailableError:
        raise MfaUnavailableError(
            "Não foi possível ler o código deste servidor — use um código de recuperação"
        ) from None


def _check_password(user: User, password: str) -> None:
    if not verify_password(password, user.password_hash):
        raise MfaError("Senha incorreta")


# ─────────────────────────── ativação ───────────────────────────


@dataclass(frozen=True)
class Setup:
    secret: str
    otpauth_uri: str


def setup(db: Session, user: User, *, password: str) -> Setup:
    """Gera um segredo novo (pendente até confirmar com um código)."""
    _check_password(user, password)
    row = _row(db, user.id, lock=True)
    if row is not None and row.enabled_at is not None:
        raise MfaError("A verificação em duas etapas já está ligada")
    secret = totp.new_secret()
    sealed = _seal(secret, user.id)
    if row is None:
        row = UserMfa(user_id=user.id)
        db.add(row)
    row.pending_secret_sealed = sealed
    db.flush()
    return Setup(secret=secret, otpauth_uri=totp.otpauth_uri(secret, account=user.email, issuer=ISSUER))


def _new_recovery_codes(db: Session, user_id: uuid.UUID) -> list[str]:
    now = datetime.now(UTC)
    for old in db.scalars(
        select(UserMfaRecoveryCode).where(
            UserMfaRecoveryCode.user_id == user_id, UserMfaRecoveryCode.used_at.is_(None)
        )
    ).all():
        old.used_at = now  # os antigos deixam de valer
    codes = []
    for _ in range(RECOVERY_CODES):
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(10))
        code = f"{raw[:5]}-{raw[5:]}"
        codes.append(code)
        db.add(UserMfaRecoveryCode(user_id=user_id, code_hash=hash_password(code)))
    db.flush()
    return codes


def enable(db: Session, user: User, *, code: str) -> list[str]:
    """Confirma o segredo pendente com um código do aplicativo. Devolve os códigos de recuperação
    (mostrados UMA vez)."""
    row = _row(db, user.id, lock=True)
    if row is None or row.pending_secret_sealed is None:
        raise MfaError("Comece a ativação de novo")
    if row.enabled_at is not None:
        raise MfaError("A verificação em duas etapas já está ligada")
    secret = _open(row.pending_secret_sealed, user.id)
    step = totp.verify(secret, code, last_step=None)
    if step is None:
        raise MfaError("Código inválido. Confira a hora do celular e tente o código atual.")
    row.secret_sealed = row.pending_secret_sealed
    row.pending_secret_sealed = None
    row.enabled_at = datetime.now(UTC)
    row.last_step = step
    db.flush()
    return _new_recovery_codes(db, user.id)


# ─────────────────────────── uso ───────────────────────────


def verify(db: Session, user_id: uuid.UUID, code: str) -> str | None:
    """Confere um código no login (TOTP ou recuperação). Devolve "totp"/"recuperacao" ou None.

    Trava a linha do usuário: dois logins simultâneos não usam o mesmo código.
    """
    row = _row(db, user_id, lock=True)
    if row is None or row.enabled_at is None or row.secret_sealed is None:
        return None
    clean = code.strip().lower().replace(" ", "")
    if clean.isdigit():
        try:
            secret = _open(row.secret_sealed, user_id)
        except MfaUnavailableError:
            return None
        step = totp.verify(secret, clean, last_step=row.last_step)
        if step is None:
            return None
        row.last_step = step
        db.flush()
        return "totp"
    if len(clean) == 10 and "-" not in clean:
        clean = f"{clean[:5]}-{clean[5:]}"
    if len(clean) != 11:
        return None
    for rc in db.scalars(
        select(UserMfaRecoveryCode)
        .where(UserMfaRecoveryCode.user_id == user_id, UserMfaRecoveryCode.used_at.is_(None))
        .with_for_update()
    ).all():
        if verify_password(clean, rc.code_hash):
            rc.used_at = datetime.now(UTC)
            db.flush()
            return "recuperacao"
    return None


def disable(db: Session, user: User, *, password: str, code: str) -> None:
    _check_password(user, password)
    if verify(db, user.id, code) is None:
        raise MfaError("Código inválido")
    row = _row(db, user.id, lock=True)
    assert row is not None
    row.secret_sealed = None
    row.pending_secret_sealed = None
    row.enabled_at = None
    row.last_step = None
    now = datetime.now(UTC)
    for rc in db.scalars(
        select(UserMfaRecoveryCode).where(
            UserMfaRecoveryCode.user_id == user.id, UserMfaRecoveryCode.used_at.is_(None)
        )
    ).all():
        rc.used_at = now
    db.flush()


def regenerate_codes(db: Session, user: User, *, password: str, code: str) -> list[str]:
    _check_password(user, password)
    if verify(db, user.id, code) is None:
        raise MfaError("Código inválido")
    return _new_recovery_codes(db, user.id)
