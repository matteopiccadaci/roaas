"""Crow deal terms for ROaaS, generated from a robot adapter and the lab's published limits (phase 4).

Outputs (written by `roaas offers build`):
  template.json  the deal template the seller adapter fills with the buyer's scope ({{placeholders}})
  seller.json    the seller adapter's configuration (hosted seller): scope rules, our job API, timing
  offers.json    the agent-readable offer catalogue: what each task is, its order schema, limits, checks, price

Scope (what a buyer sends to /quote; the adapter takes only strings and integers):
  task         "joint_waypoints" | "repeatability"
  orderText    the order exactly as the lab will hash and store it (roaas-task/1, compact JSON)
  nonce        the order's 32-hex nonce (lowercase)
  orderSha256  SHA-256 of orderText's UTF-8 bytes
Our job API refuses a quote whose fields disagree, so the filled terms always name one consistent order.

Checks: deterministic only (checks-only template). Every number in the terms is an integer (Crow ADR-0022).
`signature_valid` ties the signed manifest, and with it the summary and the preview series, to the lab's key.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any, Dict, Optional

from ..core.canonical import sha256_hex
from ..core.packaging import CIPHER, FORMAT
from ..core.tasks import parse_task

SCOPE_RULES = {
    "task": {"type": "string", "pattern": "^(joint_waypoints|repeatability)$"},
    "orderText": {"type": "string", "pattern": "^\\{[\\s\\S]{20,3000}\\}\\s*$"},
    "nonce": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
    "orderSha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
}
B64 = "^[A-Za-z0-9+/]+={0,2}$"
TIMING = {"offerExpiresInSec": 3600, "fundWithinSec": 7200, "deliverWithinSec": 259200,
          "judgeWithinSec": 600, "fallbackWithinSec": 600, "appealWindowSec": 0, "appealDecideSec": 600}
MIN_DELIVER_WITHIN_SEC = 7201          # the operators refuse 7200 or less (fundBy < deliverBy), D18
JOB_API_KEY_ENV, JOB_API_KEY_HEADER = "ROAAS_JOB_API_KEY", "X-ROaaS-Key"


def _obj(name: str, node: dict) -> dict:
    return {"type": "object", "required": [name], "properties": {name: node}}


def manifest_check(cid: str, path, leaf: dict) -> dict:
    """A json_schema check on one field of the signed manifest (the object signature_valid ties to the key)."""
    node = leaf
    for name in reversed(path):
        node = _obj(name, node)
    return {"id": cid, "type": "json_schema", "required": True, "schema": _obj("manifest", node)}


def template(adapter, fingerprint: str, limits: Optional[dict] = None, rehearsal: bool = False) -> Dict[str, Any]:
    """The deal template for one robot adapter. `fingerprint`: SHA-256 hex of the lab key's DER SPKI.
    `rehearsal`: terms for a run on the simulator only (never for a deal), as in Crow's pilot bridge."""
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint or ""):
        raise ValueError("fingerprint must be 64 lowercase hex characters (the lab key's SHA-256)")
    bounds = adapter.check_bounds(limits)
    series = list(adapter.preview_series)
    preview_props = {"t_ms": {"type": "array", "minItems": 20, "maxItems": adapter.max_preview_samples,
                              "items": {"type": "integer", "minimum": bounds["t_ms"][0], "maximum": bounds["t_ms"][1]}}}
    for s in series[1:]:
        lo, hi = bounds[s]
        preview_props[s] = {"type": "array", "items": {"type": "integer", "minimum": lo, "maximum": hi}}
    checks = [
        {"id": "format", "type": "json_schema", "required": True, "schema": {
            "type": "object", "additionalProperties": False,
            "required": ["format", "manifest", "manifest_text", "manifest_sig", "pubkey_spki_b64", "order_text",
                         "full_telemetry"],
            "properties": {
                "format": {"const": FORMAT},
                "manifest": {"type": "object"},
                # no pattern on long strings: the adjudicators' pattern engine reads at most 10,000 characters
                "manifest_text": {"type": "string", "minLength": 100, "maxLength": 1_000_000},
                "manifest_sig": {"type": "string", "pattern": B64, "minLength": 80, "maxLength": 120},
                "pubkey_spki_b64": {"type": "string", "pattern": B64, "minLength": 100, "maxLength": 200},
                "order_text": {"type": "string", "minLength": 20, "maxLength": 4000},
                "full_telemetry": {"type": "object", "additionalProperties": False,
                                   "required": ["url", "cipher", "key", "ciphertext_sha256", "bytes", "plaintext"],
                                   "properties": {
                                       "url": {"type": "string", "pattern": "^https://[A-Za-z0-9.-]{3,100}/[A-Za-z0-9._~/-]{1,300}$"},
                                       "cipher": {"const": CIPHER},
                                       "key": {"type": "string", "pattern": "^[A-Za-z0-9+/]{43}=$"},
                                       "ciphertext_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                                       "bytes": {"type": "integer", "minimum": 29, "maximum": 200_000_000},
                                       "plaintext": {"const": "telemetry.csv.gz"},
                                       "available_until": {"type": "string", "maxLength": 40}}},
            }}},
        {"id": "order-text", "type": "json_schema", "required": True,
         "schema": _obj("order_text", {"const": "{{orderText}}"})},
        {"id": "manifest-signature", "type": "signature_valid", "required": True, "alg": "ecdsa-p256-sha256",
         "publicKey": {"pointer": "/pubkey_spki_b64", "format": "spki-base64", "spkiSha256": f"sha256:{fingerprint}"},
         "message": {"pointer": "/manifest_text", "encoding": "utf8", "jsonEquals": "/manifest"},
         "signature": {"pointer": "/manifest_sig", "encoding": "base64", "format": "der"}},
        {"id": "order", "type": "json_schema", "required": True, "schema": _obj("manifest", {
            "type": "object", "required": ["order", "task"], "properties": {
                "order": {"type": "object", "required": ["nonce", "order_sha256"], "properties": {
                    "nonce": {"const": "{{nonce}}"}, "order_sha256": {"const": "{{orderSha256}}"}}},
                "task": {"type": "object", "required": ["robot", "task"], "properties": {
                    "robot": {"const": adapter.robot_id}, "task": {"const": "{{task}}"}}}}})},
        {"id": "run", "type": "json_schema", "required": True, "schema": _obj("manifest", {
            "type": "object", "required": ["schema", "status", "program", "recorder", "signer", "summary"],
            "properties": {
                "schema": {"const": adapter.manifest_schema},
                "status": {"const": "completed"},
                "program": _obj("record_only", {"const": False}),
                "recorder": _obj("simulator", {"const": rehearsal}),
                "signer": _obj("pubkey_sha256", {"const": fingerprint}),
                "summary": {"type": "object", "required": ["all_ok", "spec_violations", "continuity_violations"],
                            "properties": {"all_ok": {"const": True},
                                           "spec_violations": {"type": "integer", "minimum": 0, "maximum": 0},
                                           "continuity_violations": {"type": "integer", "minimum": 0, "maximum": 0}}}}})},
        manifest_check("arrivals", ["summary", "arrivals"], {
            "type": "array", "minItems": 3, "maxItems": adapter.max_targets,
            "items": {"type": "object", "required": ["reached", "sample", "max_error_deg", "t_ms"],
                      "properties": {"reached": {"const": True}, "sample": {"type": "integer", "minimum": 0},
                                     "max_error_deg": {"type": "number", "minimum": 0, "maximum": 1},
                                     "t_ms": {"type": "integer", "minimum": 0}}}}),
        manifest_check("samples", ["stats", "samples"], {"type": "integer", "minimum": 125, "maximum": adapter.max_samples}),
        manifest_check("preview-bounds", ["preview"], {"type": "object", "required": series, "properties": preview_props}),
        {"id": "preview-lengths", "type": "equal_lengths", "required": True,
         "series": [{"pointer": f"/manifest/preview/{s}"} for s in series]},
        {"id": "preview-time", "type": "monotonic_column", "required": True,
         "series": {"pointer": "/manifest/preview/t_ms"}, "minStep": 1, "maxStep": adapter.max_preview_step_ms},
        {"id": "hidden", "type": "hidden_characters_max", "required": True, "max": {"total": 0}},
    ]
    description = (
        f"One recorded run of a {adapter.model} robot (task {{{{task}}}}), made after this order and delivered as one "
        f"JSON document, {FORMAT}. The order is the buyer's: nonce {{{{nonce}}}}, order SHA-256 {{{{orderSha256}}}}, "
        "carried byte for byte in order_text. The document holds the lab's manifest twice, as the exact text the lab's "
        "key signed (manifest_text, ECDSA P-256, signature in manifest_sig, key in pubkey_spki_b64) and as the same "
        "object (manifest), which names this order, a completed run on the robot (not a simulator), every program "
        "target reached within 1 degree, no datasheet or continuity violation, and a preview of the recording as "
        "integer series (time in ms, joint positions in millidegrees, joint speeds in millidegrees per second) inside "
        "the lab's published envelope. The full 125 Hz telemetry (telemetry.csv.gz, its SHA-256 in the signed "
        "manifest) is linked from full_telemetry, encrypted; its key is in this document, so the buyer can read it "
        "once the deal is released. The signer key's fingerprint is given to the buyer separately.")
    if rehearsal:
        description = "REHEARSAL ONLY, NOT A REAL DEAL: a run on the robot's simulator. " + description.replace(
            "a completed run on the robot (not a simulator)", "a completed run on the simulator")
    return {"deliverable": {"kind": "dataset", "description": description, "format": "application/json"},
            "checks": checks}


def adapter_config(job_api_base: str, payout_address: str = "PLACEHOLDER", timing: Optional[dict] = None,
                   quote_requests_per_minute: int = 30) -> Dict[str, Any]:
    t = dict(TIMING, **(timing or {}))
    if t["deliverWithinSec"] < MIN_DELIVER_WITHIN_SEC:
        raise ValueError(f"deliverWithinSec must be at least {MIN_DELIVER_WITHIN_SEC} (operators refuse 7200 or less)")
    base = job_api_base.rstrip("/") + "/"
    return {
        "template": "template.json",
        "scope": SCOPE_RULES,
        "sellerApi": {"baseUrl": base, "auth": {"env": JOB_API_KEY_ENV, "header": JOB_API_KEY_HEADER},
                      "quote": f"{base}api/crow/quote", "start": f"{base}api/crow/jobs",
                      "result": f"{base}api/crow/jobs/{{jobId}}/result"},
        "quoteRateLimit": {"overall": {"requests": quote_requests_per_minute, "windowSec": 60}},
        "signingKey": "standing",
        "payoutAddress": payout_address,
        "timing": t,
        "pollMs": 3000,
        "redelivery": {"refetchEverySec": 60},
    }


def scope_for_order(order_raw: bytes) -> Dict[str, str]:
    """The scope a buyer sends for one order. Raises if the order cannot travel as a Crow scope."""
    task, _ = parse_task(order_raw)
    text = order_raw.decode("utf-8")
    scope = {"task": task.task, "orderText": text, "nonce": task.nonce, "orderSha256": sha256_hex(order_raw)}
    problems = [k for k, r in SCOPE_RULES.items() if not re.fullmatch(r["pattern"], scope[k])]
    if problems:
        raise ValueError(f"order cannot be offered through Crow: {', '.join(problems)} "
                         "(nonce: 32 lowercase hex; order: 22 to 3002 characters of JSON)")
    return scope


# The seller adapter's filler (Crow examples/seller-adapter-core.ts `fill`), for previews and tests.
_WHOLE = re.compile(r"^\{\{([A-Za-z][A-Za-z0-9]*)(\.length)?\}\}$")
_ANY = re.compile(r"(\\?)\{\{([^{}]*)\}\}")


def fill(node, scope):
    if isinstance(node, str):
        m = _WHOLE.match(node)
        if m:
            v = scope[m.group(1)]
            return copy.deepcopy(len(v) if m.group(2) else v)

        def rep(mm):
            if mm.group(1):
                return mm.group(0)[1:]
            inner = re.fullmatch(r"([A-Za-z][A-Za-z0-9]*)(\.length)?", mm.group(2))
            if not inner:
                return mm.group(0)
            v = scope[inner.group(1)]
            v = len(v) if inner.group(2) else v
            return ", ".join(map(str, v)) if isinstance(v, list) else str(v)
        return _ANY.sub(rep, node)
    if isinstance(node, list):
        return [fill(n, scope) for n in node]
    if isinstance(node, dict):
        return {k: fill(v, scope) for k, v in node.items()}
    return node


def terms_for(tmpl: dict, scope: dict) -> Dict[str, Any]:
    """A terms document as Crow's check-delivery.ts reads it: deliverable + acceptance (checks-only)."""
    filled = fill(tmpl, scope)
    return {"deliverable": filled["deliverable"],
            "acceptance": {"deterministicChecks": filled["checks"], "rubric": {"passThreshold": 0, "criteria": []}}}


def floats_in(node, path="") -> list:
    """Every non-integer number in a document: terms must hold none (Crow ADR-0022)."""
    if isinstance(node, bool):
        return []
    if isinstance(node, float):
        return [path]
    if isinstance(node, list):
        return [p for i, n in enumerate(node) for p in floats_in(n, f"{path}[{i}]")]
    if isinstance(node, dict):
        return [p for k, v in node.items() for p in floats_in(v, f"{path}.{k}")]
    return []


def offers_catalog(adapter, pricing: dict, job_api_base: str, limits: Optional[dict] = None) -> Dict[str, Any]:
    """Agent-readable description of every task the adapter offers, generated, not hand-written."""
    lim = limits or adapter.public_limits()
    offers = []
    for name, summary in adapter.task_types().items():
        p = pricing.get(name, {})
        offers.append({
            "id": f"{adapter.robot_id}/{name}",
            "robot": {"id": adapter.robot_id, "model": adapter.model,
                      "controller_firmware": lim.get("controller_firmware")},
            "task": name,
            "summary": summary,
            "order": {"schema": "roaas-task/1", "params_schema": adapter.params_schema(name, lim),
                      "example": adapter.example_order(name)},
            "limits": lim,
            "duration": adapter.duration_note(),
            "deliverable": {"format": FORMAT, "media_type": "application/json",
                            "contents": "signed manifest (text + object), signature, public key, order text, "
                                        "integer preview series, link and key to the full encrypted 125 Hz telemetry"},
            "checks": "deterministic only: see the deal template (template.json)",
            "price": {"token": pricing.get("token", "PLACEHOLDER"), "formula": p.get("formula", "PLACEHOLDER"),
                      "amounts_base_units": {k: v for k, v in p.items() if k != "formula"}},
            "deliver_within_sec": pricing.get("deliver_within_sec", TIMING["deliverWithinSec"]),
            "how_to_order": {
                "scope": {"task": name, "orderText": "<the order, compact JSON>", "nonce": "<the order's nonce>",
                          "orderSha256": "<sha256 of orderText>"},
                "validate_first": job_api_base.rstrip("/") + "/api/validate",
                "fingerprint": "sent by the lab out of band; it is pinned in every deal's signature check"},
        })
    return {"provider": "ROaaS", "network": "Ethereum Sepolia testnet (Crow Test USDC, no value)", "offers": offers}


def dumps(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
