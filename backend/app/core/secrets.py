from __future__ import annotations

import base64
import hashlib
import hmac
import os

from cryptography.fernet import Fernet, InvalidToken

_PREFIX = "fernet1:"
_LEGACY_PREFIX = "enc1:"


def _normalize_secret(raw_secret: str | None) -> bytes:
    seed = raw_secret or "posterpro-local-secret"
    return hashlib.sha256(seed.encode("utf-8")).digest()


def _fernet(secret_key: str | None) -> Fernet:
    normalized = _normalize_secret(secret_key)
    return Fernet(base64.urlsafe_b64encode(normalized))


def _keystream(secret: bytes, nonce: bytes, length: int) -> bytes:
    output = bytearray()
    counter = 0
    while len(output) < length:
        block = hmac.new(secret, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        output.extend(block)
        counter += 1
    return bytes(output[:length])


def encrypt_secret(value: str | None, *, secret_key: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    payload = _fernet(secret_key).encrypt(trimmed.encode("utf-8")).decode("utf-8")
    return f"{_PREFIX}{payload}"


def _decrypt_legacy_secret(value: str, *, secret_key: str | None) -> str:
    secret = _normalize_secret(secret_key)
    decoded = base64.urlsafe_b64decode(value[len(_LEGACY_PREFIX) :].encode("utf-8"))
    nonce = decoded[:16]
    ciphertext = decoded[16:]
    stream = _keystream(secret, nonce, len(ciphertext))
    plaintext = bytes(left ^ right for left, right in zip(ciphertext, stream, strict=True))
    return plaintext.decode("utf-8")


def decrypt_secret_if_needed(value: str | None, *, secret_key: str | None = None) -> str | None:
    if value is None or not value:
        return None
    if value.startswith(_PREFIX):
        try:
            return _fernet(secret_key).decrypt(value[len(_PREFIX) :].encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Stored secret could not be decrypted with the current session secret") from exc
    if value.startswith(_LEGACY_PREFIX):
        return _decrypt_legacy_secret(value, secret_key=secret_key)
    if not value.startswith(_PREFIX):
        return value
    return value


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    suffix = value[-4:] if len(value) > 4 else value
    return f"{'*' * max(0, len(value) - len(suffix))}{suffix}"
