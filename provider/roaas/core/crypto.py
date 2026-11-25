"""Encryption of the full telemetry kept outside the Crow delivery (decision D14).

Format, the same as Crow's deliveries: iv (12 bytes) || AES-256-GCM(plaintext) || tag (16 bytes), no
associated data, with a fresh random 32-byte key per file.
"""
from __future__ import annotations

import base64
import os
from typing import Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def encrypt(plaintext: bytes) -> Tuple[bytes, bytes]:
    """Returns (ciphertext, key)."""
    key = AESGCM.generate_key(bit_length=256)
    iv = os.urandom(12)
    return iv + AESGCM(key).encrypt(iv, plaintext, None), key


def decrypt(ciphertext: bytes, key: bytes) -> bytes:
    if len(key) != 32 or len(ciphertext) < 12 + 16:
        raise ValueError("bad key or ciphertext length")
    return AESGCM(key).decrypt(ciphertext[:12], ciphertext[12:], None)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def unb64(text: str) -> bytes:
    return base64.b64decode(text, validate=True)
