"""Ed25519 request signing: the pure part. No I/O, no clock; the API layer supplies both."""
import hashlib

import pytest

from dwarpal.signing import (HEADER_NONCE, HEADER_SIGNATURE, HEADER_TIMESTAMP, SIGNING_ALG, canonical_string,
                             generate_keypair, load_public_key, sign, verify)


def test_keypair_is_base64_and_public_key_loads():
    private_b64, public_b64 = generate_keypair()
    assert private_b64 != public_b64 and len(public_b64) == 44 and len(private_b64) == 44
    assert load_public_key(public_b64) is not None
    for bad in ("", "not base64!", "AAAA", "A" * 45):
        with pytest.raises(ValueError):
            load_public_key(bad)


def test_canonical_string_covers_time_nonce_method_target_and_body_hash():
    body = b'{"items":[{"id":"prod_shoes","quantity":1}]}'
    s = canonical_string(1_756_900_000, "n-1", "post", "/agent/v1/checkout_sessions", body)
    assert s == ("1756900000\nn-1\nPOST\n/agent/v1/checkout_sessions\n"
                 + hashlib.sha256(body).hexdigest()).encode("utf-8")
    assert canonical_string(1, "n", "GET", "/agent/v1/products?q=bottle", b"") != \
        canonical_string(1, "n", "GET", "/agent/v1/products", b"")


def test_sign_then_verify_round_trip_and_every_field_is_load_bearing():
    private_b64, public_b64 = generate_keypair()
    args = (1_756_900_000, "nonce-1", "POST", "/agent/v1/checkout_sessions", b'{"items":[]}')
    sig = sign(private_b64, *args)
    assert verify(public_b64, sig, *args)
    assert not verify(public_b64, sig, 1_756_900_001, *args[1:])            # other time
    assert not verify(public_b64, sig, args[0], "nonce-2", *args[2:])       # other nonce
    assert not verify(public_b64, sig, args[0], args[1], "GET", *args[3:])  # other method
    assert not verify(public_b64, sig, *args[:3], "/agent/v1/products", args[4])  # other target
    assert not verify(public_b64, sig, *args[:4], b'{"items":[{"id":"x"}]}')      # other body
    _, other_public = generate_keypair()
    assert not verify(other_public, sig, *args)                              # other key
    assert not verify(public_b64, "garbage", *args)                          # not even a signature


def test_header_names_and_alg_are_fixed_strings():
    assert SIGNING_ALG == "ed25519"
    assert (HEADER_TIMESTAMP, HEADER_NONCE, HEADER_SIGNATURE) == \
        ("X-Agent-Timestamp", "X-Agent-Nonce", "X-Agent-Signature")
