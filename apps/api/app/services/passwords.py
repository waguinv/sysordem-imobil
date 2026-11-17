"""Senha: troca, redefinição por e-mail e convite.

- Links de uso único: token aleatório de 256 bits; no banco só o SHA-256 (quem lê o banco não usa o link).
  "redefinir" vale PASSWORD_RESET_MINUTES (30); "convite" vale INVITE_DAYS (7). Pedir outro invalida o
  anterior do mesmo tipo.
- Trocar ou redefinir a senha grava `users.password_changed_at`: todo token de acesso emitido antes deixa
  de valer (sessões em outros aparelhos caem). A verificação em duas etapas continua valendo.
- "Esqueci minha senha" responde sempre igual (não revela se o e-mail existe) e tem limite por e-mail e
  geral, em memória do processo.
"""

import hashlib
import secrets
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_password, verify_password
from app.models import PasswordToken, User

MIN_LENGTH = 12
_COMMON = {"123456789012", "senha1234567", "password1234", "qwertyuiopas", "111111111111", "abcdefghijkl"}


class PasswordError(Exception):
    """Erro de regra (mensagem pronta para o usuário)."""


def validate(new_password: str, *, email: str) -> None:
    pw = new_password
    if len(pw) < MIN_LENGTH:
        raise PasswordError(f"A senha precisa de pelo menos {MIN_LENGTH} caracteres")
    if len(pw) > 256:
        raise PasswordError("Senha longa demais")
    low = pw.lower()
    if low in _COMMON or len(set(pw)) < 4:
        raise PasswordError("Senha fácil demais de adivinhar")
    if email and (low == email.lower() or low == email.lower().split("@")[0]):
        raise PasswordError("A senha não pode ser o seu e-mail")


def _set_password(user: User, new_password: str) -> None:
    user.password_hash = hash_password(new_password)
    user.password_changed_at = datetime.now(UTC)


def password_version(user: User) -> int:
    """Instante da última troca de senha em microssegundos (0 = nunca trocou). Vai em cada token."""
    changed = user.password_changed_at
    if changed is None:
        return 0
    return int(changed.timestamp()) * 1_000_000 + changed.microsecond


def token_still_valid(user: User, token_version: int) -> bool:
    """Comparação exata (não por horário): o token só vale se a senha não mudou desde a emissão.
    Tokens antigos, sem a versão (0), só valem se a senha nunca foi trocada."""
    return token_version == password_version(user)


def change(db: Session, user: User, *, current: str, new: str) -> None:
    if not verify_password(current, user.password_hash):
        raise PasswordError("Senha atual incorreta")
    if verify_password(new, user.password_hash):
        raise PasswordError("A nova senha precisa ser diferente da atual")
    validate(new, email=user.email)
    _set_password(user, new)
    db.flush()


# ─────────────────────────── links de uso único ───────────────────────────


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_token(db: Session, user: User, *, purpose: str, tenant_id: uuid.UUID | None) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    for old in db.scalars(
        select(PasswordToken).where(
            PasswordToken.user_id == user.id,
            PasswordToken.purpose == purpose,
            PasswordToken.used_at.is_(None),
        )
    ).all():
        old.used_at = now  # o link anterior do mesmo tipo deixa de valer
    raw = secrets.token_urlsafe(32)
    ttl = (
        timedelta(minutes=s.password_reset_minutes)
        if purpose == "redefinir"
        else timedelta(days=s.invite_days)
    )
    db.add(
        PasswordToken(
            user_id=user.id, purpose=purpose, token_hash=_hash(raw), tenant_id=tenant_id, expires_at=now + ttl
        )
    )
    db.flush()
    return raw


@dataclass(frozen=True)
class TokenInfo:
    token: PasswordToken
    user: User


def inspect(db: Session, raw: str, *, lock: bool = False) -> TokenInfo | None:
    if not raw or len(raw) > 200:
        return None
    stmt = select(PasswordToken).where(PasswordToken.token_hash == _hash(raw))
    t = db.scalar(stmt.with_for_update() if lock else stmt)
    if t is None or t.used_at is not None or t.expires_at <= datetime.now(UTC):
        return None
    user = db.get(User, t.user_id)
    if user is None or not user.is_active:
        return None
    return TokenInfo(t, user)


def consume(db: Session, raw: str, *, new_password: str) -> TokenInfo:
    info = inspect(db, raw, lock=True)  # trava: dois envios do mesmo link não passam juntos
    if info is None:
        raise PasswordError("Link inválido ou expirado. Peça um novo.")
    validate(new_password, email=info.user.email)
    _set_password(info.user, new_password)
    info.token.used_at = datetime.now(UTC)
    db.flush()
    return info


def link(raw: str) -> str:
    base = (get_settings().public_web_url or "").rstrip("/")
    return f"{base}/redefinir-senha?token={raw}"


def find_user(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))


# ─────────────────────────── limite do "esqueci minha senha" ───────────────────────────

_hits: dict[str, deque[float]] = defaultdict(deque)
_lock = Lock()
PER_EMAIL_PER_HOUR = 3
GLOBAL_PER_HOUR = 100


def reset_throttle() -> None:
    with _lock:
        _hits.clear()


def allow_request(email: str) -> bool:
    now = time.monotonic()
    with _lock:
        ok = True
        for key, limit in ((f"e:{email.lower()}", PER_EMAIL_PER_HOUR), ("global", GLOBAL_PER_HOUR)):
            q = _hits[key]
            while q and now - q[0] > 3600:
                q.popleft()
            if len(q) >= limit:
                ok = False
        if ok:
            _hits[f"e:{email.lower()}"].append(now)
            _hits["global"].append(now)
        return ok


# ─────────────────────────── textos dos e-mails ───────────────────────────


def reset_email(user: User, raw: str) -> tuple[str, str]:
    minutes = get_settings().password_reset_minutes
    return (
        "Redefinir sua senha — SysOrdem Imobil",
        f"Olá, {user.full_name}.\n\n"
        "Recebemos um pedido para redefinir a senha da sua conta no SysOrdem Imobil.\n"
        f"Para criar uma senha nova, abra o link abaixo (vale por {minutes} minutos e só uma vez):\n\n"
        f"{link(raw)}\n\n"
        "Se não foi você, ignore este e-mail: sua senha continua a mesma.\n",
    )


def invite_email(user: User, raw: str, *, company: str, invited_by: str) -> tuple[str, str]:
    days = get_settings().invite_days
    return (
        f"Convite para {company} — SysOrdem Imobil",
        f"Olá, {user.full_name}.\n\n"
        f"{invited_by} convidou você para acessar {company} no SysOrdem Imobil.\n"
        f"Para criar sua senha, abra o link abaixo (vale por {days} dias e só uma vez):\n\n"
        f"{link(raw)}\n\n"
        "Se você não esperava este convite, ignore este e-mail.\n",
    )


def access_added_email(user: User, *, company: str, invited_by: str, slug: str) -> tuple[str, str]:
    base = (get_settings().public_web_url or "").rstrip("/")
    return (
        f"Novo acesso: {company} — SysOrdem Imobil",
        f"Olá, {user.full_name}.\n\n"
        f"{invited_by} deu a você acesso a {company} no SysOrdem Imobil.\n"
        f'Entre com o seu e-mail e a senha que você já usa, informando a empresa "{slug}":\n\n'
        f"{base}/entrar\n",
    )
