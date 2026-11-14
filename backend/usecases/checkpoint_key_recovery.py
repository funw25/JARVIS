"""Experimental portable checkpoint signing-key recovery; no file or OS storage.

Only caller-supplied in-memory keys and recovery packages are handled. This is
not a production key manager, does not establish public-key trust, and has not
been independently reviewed.
"""
import hmac
import re
import secrets

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .audit_chain import canonical_bytes

FORMAT = "jarvis-checkpoint-key-recovery-v1"
KDF = {"name": "argon2id", "memory_kib": 65536, "iterations": 3, "parallelism": 2}
FIELDS = {"format", "kdf", "salt", "nonce", "public_key", "ciphertext"}
HEX = re.compile(r"[0-9a-f]+\Z")


class RecoveryError(RuntimeError):
    pass


def _password(value):
    if not isinstance(value, str) or len(value) < 16:
        raise RecoveryError("recovery passphrase must have at least 16 characters")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise RecoveryError("recovery passphrase is invalid UTF-8") from exc
    if len(encoded) > 4096:
        raise RecoveryError("recovery passphrase is too long")
    return encoded


def _hex(value, size):
    if not isinstance(value, str) or len(value) != size * 2 or not HEX.fullmatch(value):
        raise RecoveryError("recovery package contains malformed bytes")
    return bytes.fromhex(value)


def _derive(passphrase, salt):
    return hash_secret_raw(passphrase, salt, time_cost=KDF["iterations"], memory_cost=KDF["memory_kib"],
                           parallelism=KDF["parallelism"], hash_len=32, type=Type.ID)


def _public(private_key):
    return private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def create_package(private_key, passphrase):
    """Return an encrypted package; caller is responsible for secure storage."""
    if not isinstance(private_key, Ed25519PrivateKey):
        raise RecoveryError("Ed25519 private key required")
    secret = _password(passphrase)
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    header = {"format": FORMAT, "kdf": dict(KDF), "salt": salt.hex(), "nonce": nonce.hex(),
              "public_key": _public(private_key).hex()}
    raw = private_key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                    serialization.NoEncryption())
    encrypted = AESGCM(_derive(secret, salt)).encrypt(nonce, raw, canonical_bytes(header))
    return {**header, "ciphertext": encrypted.hex()}


def recover_package(package, passphrase, *, expected_public_key):
    """Recover only if the caller independently supplies the expected public key."""
    secret = _password(passphrase)
    if not isinstance(package, dict) or set(package) != FIELDS or package.get("format") != FORMAT:
        raise RecoveryError("unsupported recovery package")
    # Exact fixed parameters prevent unauthenticated KDF downgrade/resource abuse.
    params = package.get("kdf")
    if not isinstance(params, dict) or set(params) != set(KDF) or any(type(params[k]) is not type(v) or params[k] != v for k, v in KDF.items()):
        raise RecoveryError("unsupported recovery KDF parameters")
    salt, nonce = _hex(package["salt"], 16), _hex(package["nonce"], 12)
    package_public = _hex(package["public_key"], 32)
    ciphertext = _hex(package["ciphertext"], 48)
    if not isinstance(expected_public_key, bytes) or len(expected_public_key) != 32:
        raise RecoveryError("independently trusted public key is required")
    if not hmac.compare_digest(package_public, expected_public_key):
        raise RecoveryError("recovery package public key does not match trusted identity")
    header = {name: package[name] for name in ("format", "kdf", "salt", "nonce", "public_key")}
    try:
        raw = AESGCM(_derive(secret, salt)).decrypt(nonce, ciphertext, canonical_bytes(header))
        private_key = Ed25519PrivateKey.from_private_bytes(raw)
    except (InvalidTag, ValueError) as exc:
        raise RecoveryError("recovery package or passphrase is invalid") from exc
    if not hmac.compare_digest(_public(private_key), expected_public_key):
        raise RecoveryError("recovered key does not match trusted identity")
    return private_key
