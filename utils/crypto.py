"""Field-level encryption for everything personal that is stored in the database.

Envelope scheme:

  master key (ENCRYPTION_MASTER_KEY, app secret, never in the database)
      └─ wraps ─► per-user data key (DEK, random 32 bytes, stored wrapped in profiles)
                      └─ encrypts ─► each résumé / cover letter / saved job (AES-256-GCM)

Anyone who can read the database sees only ciphertext and wrapped keys. Every
ciphertext is bound (as GCM associated data) to the user and the row it belongs
to, so a blob copied onto another row or another user fails to decrypt.
Deleting a user deletes their wrapped DEK, which makes any leftover copy of their
data (e.g. an old database backup) permanently unreadable.

No Streamlit imports here -- pure functions, easy to test.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = "v1"
_NONCE_BYTES = 12


class CryptoError(Exception):
    """Bad key, tampered data, or data that belongs to someone else."""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text.encode("ascii"))


def parse_master_key(value: str) -> bytes:
    """Decode a base64 master key; it must be exactly 32 bytes."""
    try:
        key = _b64d(value.strip())
    except (binascii.Error, ValueError, UnicodeEncodeError) as exc:
        raise CryptoError("ENCRYPTION_MASTER_KEY is not valid base64.") from exc
    if len(key) != 32:
        raise CryptoError("ENCRYPTION_MASTER_KEY must decode to exactly 32 bytes.")
    return key


def generate_master_key() -> str:
    """A fresh master key, ready to paste into secrets."""
    return _b64e(os.urandom(32))


def new_dek() -> bytes:
    return os.urandom(32)


def _seal(key: bytes, aad: str, plaintext: bytes) -> str:
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad.encode("utf-8"))
    return f"{_VERSION}.{_b64e(nonce + ciphertext)}"


def _open(key: bytes, aad: str, token: str) -> bytes:
    try:
        version, _, body = token.partition(".")
        if version != _VERSION or not body:
            raise CryptoError("Unknown ciphertext format.")
        raw = _b64d(body)
        nonce, ciphertext = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
        return AESGCM(key).decrypt(nonce, ciphertext, aad.encode("utf-8"))
    except InvalidTag as exc:
        raise CryptoError("Decryption failed (wrong key or data was altered).") from exc
    except (binascii.Error, ValueError) as exc:
        raise CryptoError("Ciphertext is malformed.") from exc


# --- Data key wrapping -------------------------------------------------------

def wrap_dek(master_key: bytes, user_id: str, dek: bytes) -> str:
    return _seal(master_key, f"dek:{user_id}", dek)


def unwrap_dek(master_key: bytes, user_id: str, wrapped: str) -> bytes:
    return _open(master_key, f"dek:{user_id}", wrapped)


# --- Row encryption ----------------------------------------------------------

def _aad(user_id: str, kind: str, row_id: str) -> str:
    return f"{user_id}:{kind}:{row_id}"


def encrypt_json(dek: bytes, user_id: str, kind: str, row_id: str, payload: Any) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _seal(dek, _aad(user_id, kind, row_id), data)


def decrypt_json(dek: bytes, user_id: str, kind: str, row_id: str, token: str) -> Any:
    return json.loads(_open(dek, _aad(user_id, kind, row_id), token).decode("utf-8"))


def keyed_hash(dek: bytes, value: str) -> str:
    """Deterministic keyed hash (HMAC-SHA256 under the user's key). Lets the
    database enforce 'one row per job URL' without ever seeing the URL."""
    digest = hmac.new(dek, value.strip().encode("utf-8"), hashlib.sha256).hexdigest()
    return digest
