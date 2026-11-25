"""Two-level delivery `roaas-episode/1` (decision D14, docs/ARCHITECTURE.md §4.4).

Level 1, the Crow delivery: ONE JSON document of at most 1 MiB that adjudicators' checks can read:
  manifest (object) + manifest_text (the exact signed bytes) + manifest_sig + pubkey_spki_b64
  + order_text (the exact order) + full_telemetry {url, key, hashes}.
  The checkable preview (integer series) is INSIDE the signed manifest: Crow's signature_valid check with
  jsonEquals /manifest ties every series its other checks read to the lab's key.
Level 2, the full 125 Hz telemetry (the kit's telemetry.csv.gz, hash in the manifest), encrypted with a fresh
AES-256-GCM key and stored by us; its key travels only inside level 1, so the buyer gets it on release.

The public key is base64 SPKI DER (no newlines anywhere in the document), a format Crow's proposed
`signature_valid` check reads (`spki-base64`).
"""
from __future__ import annotations

import base64
import datetime as dt
import json
from pathlib import Path
from typing import Optional, Tuple

from cryptography.hazmat.primitives import serialization

from . import crypto
from .canonical import sha256_hex

FORMAT = "roaas-episode/1"
CIPHER = "aes-256-gcm:iv12|ciphertext|tag16"
MAX_DELIVERY_BYTES = 1_000_000          # Crow: 1 048 576 bytes of ciphertext, minus IV, tag and margin
RETENTION_DAYS = 30                     # placeholder: how long we keep the full telemetry for the buyer


def spki_b64_from_pem(pem: bytes) -> str:
    key = serialization.load_pem_public_key(pem)
    return base64.b64encode(key.public_bytes(serialization.Encoding.DER,
                                             serialization.PublicFormat.SubjectPublicKeyInfo)).decode()


def pem_from_spki_b64(spki_b64: str) -> bytes:
    key = serialization.load_der_public_key(base64.b64decode(spki_b64, validate=True))
    return key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def build_delivery(episode_dir: Path, *, telemetry_url: str, available_until: Optional[str] = None
                   ) -> Tuple[bytes, bytes]:
    """Returns (delivery_json_bytes, encrypted_full_telemetry). Upload the second to `telemetry_url`.
    `available_until` (ISO date) is our promise to keep the full telemetry; default: today + RETENTION_DAYS."""
    if not available_until:
        available_until = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d")
    ep = Path(episode_dir)
    manifest_bytes = (ep / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("status") != "completed":
        raise ValueError(f"episode status is {manifest.get('status')}: an aborted run is never delivered")
    telemetry = (ep / "telemetry.csv.gz").read_bytes()
    ciphertext, key = crypto.encrypt(telemetry)
    doc = {
        "format": FORMAT,
        "manifest": manifest,
        "manifest_text": manifest_bytes.decode("utf-8"),
        "manifest_sig": (ep / "manifest.sig").read_text().strip(),
        "pubkey_spki_b64": spki_b64_from_pem((ep / "pubkey.pem").read_bytes()),
        "order_text": (ep / "order.json").read_bytes().decode("utf-8"),
        "full_telemetry": {
            "url": telemetry_url,
            "cipher": CIPHER,
            "key": crypto.b64(key),
            "ciphertext_sha256": sha256_hex(ciphertext),
            "bytes": len(ciphertext),
            "plaintext": "telemetry.csv.gz",
            "available_until": available_until,
        },
    }
    out = json.dumps(doc, separators=(",", ":"), ensure_ascii=True).encode()
    if len(out) > MAX_DELIVERY_BYTES:
        raise ValueError(f"delivery is {len(out)} bytes; at most {MAX_DELIVERY_BYTES}")
    return out, ciphertext


def reconstruct_episode_dir(doc: dict, telemetry_gz: bytes, dest: Path) -> Path:
    """Rebuild the kit-style episode folder from a delivery and the decrypted telemetry, so that the
    kit's own verify_episode.py (or ours) can check it. Every file is byte-identical to the lab's."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "manifest.json").write_bytes(doc["manifest_text"].encode("utf-8"))
    (dest / "manifest.sig").write_text(doc["manifest_sig"])
    (dest / "pubkey.pem").write_bytes(pem_from_spki_b64(doc["pubkey_spki_b64"]))
    (dest / "order.json").write_bytes(doc["order_text"].encode("utf-8"))
    (dest / "telemetry.csv.gz").write_bytes(telemetry_gz)
    return dest
