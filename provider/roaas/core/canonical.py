"""Canonical JSON and hashing helpers, byte-compatible with the dobot kit's manifest signing."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def canonical(obj) -> bytes:
    """UTF-8 JSON with sorted keys and no whitespace: the exact form the kit signs."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
