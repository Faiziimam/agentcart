"""On-device vault.

The vault holds the Ed25519 *policy signing key*, encrypted with a key derived from the user's
passphrase (scrypt, N=2^17) using AES-256-GCM. The public key is stored in clear and bound to
the ciphertext as associated data.

Only an interactive human (TTY + passphrase) can sign a new spending policy. The MCP server, and
therefore the AI model, can only *verify* the policy -- it can never loosen it.

By design the vault refuses to store UPI PINs, card numbers, CVVs or OTPs: no legitimate flow
needs them and storing them would shift fraud liability to the user.
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from . import paths
from .errors import TamperError, VaultError

FORMAT = 1
DEFAULT_KDF = {"n": 2**17, "r": 8, "p": 1}
MIN_PASSPHRASE = 12

_FORBIDDEN_KEYS = re.compile(r"(^|[_\-\s])(upi_?pin|mpin|pin|cvv|cvc|otp|card_?number|pan)($|[_\-\s])", re.I)


def b64e(b: bytes) -> str:
    return base64.b64encode(b).decode()


def b64d(s: str) -> bytes:
    return base64.b64decode(s.encode(), validate=True)


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def assert_storable(key: str, value: str) -> None:
    """Reject secrets agentcart must never hold."""
    if _FORBIDDEN_KEYS.search(key):
        raise VaultError(f"refusing to store '{key}': PINs, CVVs, OTPs and card numbers are never stored")
    compact = re.sub(r"[\s-]", "", value)
    if compact.isdigit() and 13 <= len(compact) <= 19 and _luhn(compact):
        raise VaultError("refusing to store what looks like a card number")


def _derive(passphrase: str, salt: bytes, kdf: dict[str, int]) -> bytes:
    return Scrypt(salt=salt, length=32, n=kdf["n"], r=kdf["r"], p=kdf["p"]).derive(passphrase.encode())


@dataclass
class UnlockedVault:
    signing_key: Ed25519PrivateKey
    data: dict[str, Any]


def exists() -> bool:
    return paths.vault_file().exists()


def create(passphrase: str, kdf: dict[str, int] | None = None) -> Ed25519PublicKey:
    if exists():
        raise VaultError("vault already exists; use 'agentcart passwd' to change the passphrase")
    if len(passphrase) < MIN_PASSPHRASE:
        raise VaultError(f"passphrase must be at least {MIN_PASSPHRASE} characters")
    sk = Ed25519PrivateKey.generate()
    data = {"genesis": secrets.token_hex(16), "created": datetime.now(UTC).isoformat(), "secrets": {}}
    _save(sk, data, passphrase, kdf or DEFAULT_KDF)
    return sk.public_key()


def _save(sk: Ed25519PrivateKey, data: dict[str, Any], passphrase: str, kdf: dict[str, int]) -> None:
    pub = sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    raw = sk.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                           serialization.NoEncryption())
    plaintext = json.dumps({"sk": b64e(raw), "data": data}).encode()
    salt, nonce = os.urandom(16), os.urandom(12)
    key = _derive(passphrase, salt, kdf)
    header = {"format": FORMAT, "kdf": {"alg": "scrypt", **kdf}, "salt": b64e(salt),
              "public_key": b64e(pub)}
    aad = json.dumps(header, sort_keys=True).encode()
    ct = AESGCM(key).encrypt(nonce, plaintext, aad)
    doc = {**header, "nonce": b64e(nonce), "ciphertext": b64e(ct)}
    paths.write_private(paths.vault_file(), json.dumps(doc, indent=2).encode())


def _load_doc() -> dict[str, Any]:
    if not exists():
        raise VaultError("no vault found; run 'agentcart init' in a terminal first")
    try:
        doc: dict[str, Any] = json.loads(paths.vault_file().read_text())
    except ValueError as e:
        raise TamperError("vault file is corrupted") from e
    if doc.get("format") != FORMAT or doc.get("kdf", {}).get("alg") != "scrypt":
        raise TamperError("unsupported or tampered vault format")
    return doc


def public_key() -> Ed25519PublicKey:
    doc = _load_doc()
    return Ed25519PublicKey.from_public_bytes(b64d(doc["public_key"]))


def unlock(passphrase: str) -> UnlockedVault:
    doc = _load_doc()
    header = {k: doc[k] for k in ("format", "kdf", "salt", "public_key")}
    kdf = {k: int(doc["kdf"][k]) for k in ("n", "r", "p")}
    if kdf["n"] < 2**14:
        raise TamperError("vault KDF parameters are too weak (tampered?)")
    key = _derive(passphrase, b64d(doc["salt"]), kdf)
    try:
        pt = AESGCM(key).decrypt(b64d(doc["nonce"]), b64d(doc["ciphertext"]),
                                 json.dumps(header, sort_keys=True).encode())
    except InvalidTag as e:
        raise VaultError("wrong passphrase (or vault was tampered with)") from e
    inner = json.loads(pt)
    sk = Ed25519PrivateKey.from_private_bytes(b64d(inner["sk"]))
    if sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw) != b64d(
            doc["public_key"]):
        raise TamperError("vault key mismatch")
    return UnlockedVault(sk, inner["data"])


def change_passphrase(old: str, new: str) -> None:
    v = unlock(old)
    if len(new) < MIN_PASSPHRASE:
        raise VaultError(f"passphrase must be at least {MIN_PASSPHRASE} characters")
    doc = _load_doc()
    kdf = {k: int(doc["kdf"][k]) for k in ("n", "r", "p")}
    _save(v.signing_key, v.data, new, kdf)


def put_secret(passphrase: str, key: str, value: str) -> None:
    assert_storable(key, value)
    v = unlock(passphrase)
    v.data.setdefault("secrets", {})[key] = value
    doc = _load_doc()
    _save(v.signing_key, v.data, passphrase, {k: int(doc["kdf"][k]) for k in ("n", "r", "p")})


def sign(sk: Ed25519PrivateKey, payload: bytes) -> str:
    return b64e(sk.sign(payload))


def verify(payload: bytes, signature: str) -> None:
    try:
        public_key().verify(b64d(signature), payload)
    except (InvalidSignature, ValueError) as e:
        raise TamperError("signature check failed: policy was modified outside agentcart") from e
