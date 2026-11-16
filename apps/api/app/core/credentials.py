"""Cofre das credenciais de PSP das empresas (chave de API do Asaas de cada empresa).

AES-256-GCM com a chave PAYMENTS_CREDENTIALS_KEY (base64 de 32 bytes, só no ambiente do servidor):
quem tem um dump do banco não tem as chaves de API. O "associated data" amarra o texto cifrado à
empresa e ao uso — copiar a chave cifrada de uma empresa para outra não decifra.

Formato guardado: "v1:" + base64url(nonce 12 bytes + texto cifrado + tag).
"""

import base64
import binascii
import os
import uuid
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

_PREFIX = "v1:"


class CredentialsUnavailableError(Exception):
    """PAYMENTS_CREDENTIALS_KEY ausente/inválida ou texto cifrado que não abre com ela."""


@lru_cache
def _aead() -> AESGCM:
    raw = get_settings().payments_credentials_key
    if not raw:
        raise CredentialsUnavailableError("PAYMENTS_CREDENTIALS_KEY não configurada neste ambiente")
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise CredentialsUnavailableError("PAYMENTS_CREDENTIALS_KEY não é base64 válido") from None
    if len(key) != 32:
        raise CredentialsUnavailableError(
            "PAYMENTS_CREDENTIALS_KEY deve ter 32 bytes (openssl rand -base64 32)"
        )
    return AESGCM(key)


def available() -> bool:
    try:
        _aead()
    except CredentialsUnavailableError:
        return False
    return True


def _ad(tenant_id: uuid.UUID, purpose: str) -> bytes:
    return f"{purpose}:{tenant_id}".encode()


def seal(plaintext: str, *, tenant_id: uuid.UUID, purpose: str) -> str:
    nonce = os.urandom(12)
    ct = _aead().encrypt(nonce, plaintext.encode(), _ad(tenant_id, purpose))
    return _PREFIX + base64.urlsafe_b64encode(nonce + ct).decode()


def open_sealed(sealed: str, *, tenant_id: uuid.UUID, purpose: str) -> str:
    if not sealed.startswith(_PREFIX):
        raise CredentialsUnavailableError("credencial em formato desconhecido")
    try:
        blob = base64.urlsafe_b64decode(sealed[len(_PREFIX) :])
        return _aead().decrypt(blob[:12], blob[12:], _ad(tenant_id, purpose)).decode()
    except (InvalidTag, binascii.Error, ValueError):
        raise CredentialsUnavailableError(
            "credencial não abre com a PAYMENTS_CREDENTIALS_KEY atual"
        ) from None
