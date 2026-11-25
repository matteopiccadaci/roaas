"""Buyer-side verification of a `roaas-episode/1` delivery.

Without the full telemetry it checks what can be checked from the document alone (which is also what Crow's
adjudicators read): signature, signed manifest = shown manifest, key fingerprint, exact order, preview hash,
preview bounds, signed summary. With the full telemetry (downloaded and decrypted with the key from the
delivery) it rebuilds the episode folder and runs every robot-specific check on the 125 Hz data.
"""
from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path
from typing import List, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from . import crypto
from .canonical import sha256_hex
from .model import CheckResult
from .packaging import FORMAT, pem_from_spki_b64, reconstruct_episode_dir


def verify_delivery(delivery: bytes, *, expected_order: Optional[bytes] = None,
                    fingerprint: Optional[str] = None, telemetry_ciphertext: Optional[bytes] = None
                    ) -> List[CheckResult]:
    from cryptography.hazmat.primitives import serialization
    from ..robots import get_adapter

    res: List[CheckResult] = []
    try:
        doc = json.loads(delivery)
        res.append(CheckResult("format", doc.get("format") == FORMAT, str(doc.get("format"))))
        manifest_text = doc["manifest_text"]
        m = json.loads(manifest_text)
    except (ValueError, KeyError, TypeError) as e:
        return res + [CheckResult("parse", False, str(e))]
    res.append(CheckResult("manifest-equals-signed-text", m == doc.get("manifest")))

    pub = serialization.load_pem_public_key(pem_from_spki_b64(doc["pubkey_spki_b64"]))
    fp = sha256_hex(base64.b64decode(doc["pubkey_spki_b64"]))
    try:
        pub.verify(base64.b64decode(doc["manifest_sig"]), manifest_text.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
        res.append(CheckResult("signature", True))
    except (InvalidSignature, ValueError):
        res.append(CheckResult("signature", False, "manifest_text is not signed by pubkey_spki_b64"))
    res.append(CheckResult("manifest-names-signer", m.get("signer", {}).get("pubkey_sha256") == fp))
    if fingerprint:
        res.append(CheckResult("fingerprint", fp == fingerprint.lower().strip(), f"key {fp}"))
    else:
        res.append(CheckResult("fingerprint", True, f"NOT pinned (test only): key {fp}"))

    order = doc.get("order_text", "").encode("utf-8")
    res.append(CheckResult("order-hash", sha256_hex(order) == m["order"]["order_sha256"]))
    if expected_order is not None:
        res.append(CheckResult("order-is-yours", order == expected_order, "byte-identical to your order"))
    res.append(CheckResult("status", m.get("status") == "completed" and m.get("summary", {}).get("all_ok") is True,
                           f"{m.get('status')}, summary all_ok={m.get('summary', {}).get('all_ok')}"))
    adapter = get_adapter(m["task"]["robot"])
    res += adapter.verify_preview(m["preview"], m)

    if telemetry_ciphertext is not None:
        ft = doc["full_telemetry"]
        res.append(CheckResult("telemetry-ciphertext-hash", sha256_hex(telemetry_ciphertext) == ft["ciphertext_sha256"]))
        try:
            plain = crypto.decrypt(telemetry_ciphertext, crypto.unb64(ft["key"]))
        except Exception as e:  # noqa: BLE001
            return res + [CheckResult("telemetry-decrypt", False, str(e))]
        res.append(CheckResult("telemetry-signed-hash", sha256_hex(plain) == m["files"]["telemetry.csv.gz"]["sha256"]))
        with tempfile.TemporaryDirectory() as d:
            ep = reconstruct_episode_dir(doc, plain, Path(d) / "episode")
            res += [CheckResult("full:" + r.id, r.ok, r.detail)
                    for r in adapter.verify_episode_dir(ep, expected_order, fingerprint)]
    return res
