"""
pd_crypto.py — PIN/pattern hashing for PortDesk (extracted module).

Built-in PBKDF2-SHA256 PIN hashing — replaces the bcrypt dependency.
OWASP 2025 minimum: 600,000 iterations. Stdlib only (hashlib, os, hmac).
Constant-time comparison via hmac.compare_digest prevents timing attacks.

This module is self-contained — no PortDesk globals required.
"""
from __future__ import annotations

import os
import hashlib
import hmac as _hmac_mod

_PIN_ROUNDS = 600_000   # OWASP 2025 minimum for PBKDF2-SHA256


def pin_hash(secret: str) -> str:
    """Hash a PIN/pattern secret with PBKDF2-SHA256.
    Returns format: pbkdf2:sha256:rounds:salt:dk"""
    salt = os.urandom(32)
    dk = hashlib.pbkdf2_hmac('sha256', secret.encode(), salt, _PIN_ROUNDS)
    return f'pbkdf2:sha256:{_PIN_ROUNDS}:{salt.hex()}:{dk.hex()}'


def pin_verify(secret: str, stored: str) -> bool:
    """Verify a PIN/pattern secret against a stored hash. Constant-time."""
    try:
        parts = stored.split(':')
        if len(parts) != 5 or parts[0] != 'pbkdf2':
            return False
        _, algo, rounds_s, salt_s, dk_s = parts
        salt = bytes.fromhex(salt_s)
        rounds = int(rounds_s)
        dk = hashlib.pbkdf2_hmac(algo, secret.encode(), salt, rounds)
        return _hmac_mod.compare_digest(dk.hex(), dk_s)
    except (ValueError, TypeError, AttributeError):
        return False
