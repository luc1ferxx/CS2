from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

AES_GCM_KEY_BYTES = 32
AES_GCM_NONCE_BYTES = 12
AES_GCM_TAG_BYTES = 16
MAX_CREDENTIAL_BYTES = 4096
MAX_CONTEXT_LENGTH = 255
KEY_VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class SteamCredentialError(RuntimeError):
    pass


@dataclass(frozen=True)
class EncryptedValue:
    ciphertext: bytes
    nonce: bytes
    key_version: str


class SteamCredentialCipher:
    def __init__(self, encoded_key: object, key_version: str | None = None):
        if key_version is None and not isinstance(encoded_key, str):
            runtime_settings = encoded_key
            encoded_key = getattr(
                runtime_settings,
                "steam_credential_encryption_key",
                None,
            )
            key_version = getattr(
                runtime_settings,
                "steam_credential_encryption_key_version",
                None,
            )
        if not isinstance(encoded_key, str) or not isinstance(key_version, str):
            raise SteamCredentialError("Steam credential encryption is not configured")
        self.key_version = _validate_key_version(key_version)
        self._key = _decode_key(encoded_key)
        self._cipher = AESGCM(self._key)

    def encrypt(
        self,
        value: str,
        *,
        owner_id: str,
        purpose: str,
        record_id: str,
    ) -> EncryptedValue:
        plaintext = _credential_bytes(value)
        nonce = os.urandom(AES_GCM_NONCE_BYTES)
        ciphertext = self._cipher.encrypt(
            nonce,
            plaintext,
            _associated_data(owner_id, purpose, record_id),
        )
        return EncryptedValue(
            ciphertext=ciphertext,
            nonce=nonce,
            key_version=self.key_version,
        )

    def decrypt(
        self,
        encrypted: EncryptedValue | None = None,
        *,
        ciphertext: bytes | None = None,
        nonce: bytes | None = None,
        key_version: str | None = None,
        owner_id: str,
        purpose: str,
        record_id: str,
    ) -> str:
        if encrypted is None:
            if (
                not isinstance(ciphertext, bytes)
                or not isinstance(nonce, bytes)
                or not isinstance(key_version, str)
            ):
                raise SteamCredentialError("Encrypted Steam credential is invalid")
            encrypted = EncryptedValue(
                ciphertext=ciphertext,
                nonce=nonce,
                key_version=key_version,
            )
        elif ciphertext is not None or nonce is not None or key_version is not None:
            raise SteamCredentialError("Encrypted Steam credential is ambiguous")
        if (
            not isinstance(encrypted.ciphertext, bytes)
            or not isinstance(encrypted.nonce, bytes)
            or not isinstance(encrypted.key_version, str)
        ):
            raise SteamCredentialError("Encrypted Steam credential is invalid")
        try:
            encrypted_key_version = _validate_key_version(encrypted.key_version)
        except SteamCredentialError as exc:
            raise SteamCredentialError("Encrypted Steam credential is invalid") from exc
        if not secrets.compare_digest(encrypted_key_version, self.key_version):
            raise SteamCredentialError("Steam credential key version is unavailable")
        if (
            len(encrypted.nonce) != AES_GCM_NONCE_BYTES
            or len(encrypted.ciphertext) < AES_GCM_TAG_BYTES
            or len(encrypted.ciphertext) > MAX_CREDENTIAL_BYTES + AES_GCM_TAG_BYTES
        ):
            raise SteamCredentialError("Encrypted Steam credential is invalid")
        try:
            plaintext = self._cipher.decrypt(
                encrypted.nonce,
                encrypted.ciphertext,
                _associated_data(owner_id, purpose, record_id),
            )
            value = plaintext.decode("utf-8")
        except (InvalidTag, UnicodeDecodeError, ValueError) as exc:
            raise SteamCredentialError(
                "Encrypted Steam credential could not be verified"
            ) from exc
        _credential_bytes(value)
        return value

    @staticmethod
    def fingerprint(value: str) -> str:
        return hashlib.sha256(_credential_bytes(value)).hexdigest()


def _decode_key(encoded_key: str) -> bytes:
    value = encoded_key.strip()
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
        raise SteamCredentialError(
            "STEAM_CREDENTIAL_ENCRYPTION_KEY must be URL-safe base64"
        )
    unpadded = value.rstrip("=")
    try:
        decoded = base64.b64decode(
            unpadded + ("=" * (-len(unpadded) % 4)),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError) as exc:
        raise SteamCredentialError(
            "STEAM_CREDENTIAL_ENCRYPTION_KEY must be URL-safe base64"
        ) from exc
    if len(decoded) != AES_GCM_KEY_BYTES:
        raise SteamCredentialError(
            "STEAM_CREDENTIAL_ENCRYPTION_KEY must decode to exactly 32 bytes"
        )
    canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
    if not secrets.compare_digest(unpadded, canonical):
        raise SteamCredentialError(
            "STEAM_CREDENTIAL_ENCRYPTION_KEY must be canonical URL-safe base64"
        )
    return decoded


def _validate_key_version(key_version: str) -> str:
    value = key_version.strip()
    if not KEY_VERSION_PATTERN.fullmatch(value):
        raise SteamCredentialError(
            "STEAM_CREDENTIAL_ENCRYPTION_KEY_VERSION is invalid"
        )
    return value


def _credential_bytes(value: str) -> bytes:
    if not isinstance(value, str):
        raise SteamCredentialError("Steam credential must be text")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise SteamCredentialError("Steam credential is invalid") from exc
    if not encoded or len(encoded) > MAX_CREDENTIAL_BYTES or "\x00" in value:
        raise SteamCredentialError("Steam credential is invalid")
    return encoded


def _associated_data(owner_id: str, purpose: str, record_id: str) -> bytes:
    values = {
        "ownerId": _context_value(owner_id),
        "purpose": _context_value(purpose),
        "recordId": _context_value(record_id),
        "schema": "steam-credential-aad-v1",
    }
    return json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _context_value(value: str) -> str:
    if not isinstance(value, str):
        raise SteamCredentialError("Steam credential context is invalid")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > MAX_CONTEXT_LENGTH
        or "\x00" in normalized
        or any(ord(character) < 32 for character in normalized)
    ):
        raise SteamCredentialError("Steam credential context is invalid")
    return normalized
