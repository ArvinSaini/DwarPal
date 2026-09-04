"""Ed25519 request signing for agents that want more than a bearer key.

An agent registers a public key. From then on every request must carry three headers, and a leaked bearer key
alone buys nothing:

    X-Agent-Timestamp: unix seconds, within MAX_SKEW_S of the merchant's clock
    X-Agent-Nonce:     any string the agent has not used before
    X-Agent-Signature: base64 Ed25519 signature over canonical_string(...)

The canonical string is ``ts \n nonce \n METHOD \n target \n sha256(body)`` where target is the path plus the
query string exactly as sent. This module is pure: the API layer supplies the clock and the nonce store.
"""
from __future__ import annotations

import base64
import binascii
import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

SIGNING_ALG = "ed25519"
HEADER_TIMESTAMP = "X-Agent-Timestamp"
HEADER_NONCE = "X-Agent-Nonce"
HEADER_SIGNATURE = "X-Agent-Signature"
MAX_SKEW_S = 300
CANONICAL_FORM = "ts\nnonce\nMETHOD\npath?query\nsha256(body)"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str, expected_len: int, what: str) -> bytes:
    try:
        raw = base64.b64decode(text or "", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{what} is not valid base64") from exc
    if len(raw) != expected_len:
        raise ValueError(f"{what} must decode to {expected_len} bytes, got {len(raw)}")
    return raw


def generate_keypair() -> tuple[str, str]:
    """(private_b64, public_b64): raw 32-byte Ed25519 keys, base64. The private half never reaches the merchant."""
    private = ed25519.Ed25519PrivateKey.generate()
    raw_private = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                        serialization.NoEncryption())
    raw_public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return _b64(raw_private), _b64(raw_public)


def load_public_key(public_b64: str) -> ed25519.Ed25519PublicKey:
    return ed25519.Ed25519PublicKey.from_public_bytes(_unb64(public_b64, 32, "public key"))


def canonical_string(ts: int, nonce: str, method: str, target: str, body: bytes) -> bytes:
    digest = hashlib.sha256(body or b"").hexdigest()
    return f"{int(ts)}\n{nonce}\n{method.upper()}\n{target}\n{digest}".encode("utf-8")


def sign(private_b64: str, ts: int, nonce: str, method: str, target: str, body: bytes) -> str:
    private = ed25519.Ed25519PrivateKey.from_private_bytes(_unb64(private_b64, 32, "private key"))
    return _b64(private.sign(canonical_string(ts, nonce, method, target, body)))


def verify(public_b64: str, signature_b64: str, ts: int, nonce: str, method: str, target: str, body: bytes) -> bool:
    try:
        public = load_public_key(public_b64)
        public.verify(_unb64(signature_b64, 64, "signature"), canonical_string(ts, nonce, method, target, body))
        return True
    except (ValueError, InvalidSignature):
        return False
