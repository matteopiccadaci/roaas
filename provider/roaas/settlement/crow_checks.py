"""The deal's own deterministic checks, run by the lab on the delivery BEFORE it is uploaded.

Crow's adjudicator runs the terms' checks on the delivery; a required check that fails refunds the buyer, and the
order is used up. Running the same checks here first turns such a delivery into a failed job the operator can retry
(`roaas ops retry`), with the reason, instead of a lost deal.

This is a port of the check kinds our templates use, written from Crow's judgment code (commit 423ffcd,
packages/judgment/src/checks): json_schema (Crow's own subset of JSON Schema), signature_valid, equal_lengths,
monotonic_column and hidden_characters_max. A kind or a schema keyword this module does not know FAILS here, so a
delivery is never uploaded on a check we could not run. tests/test_crow_checks.py compares the verdicts with Crow's
own check-delivery.ts when CROW_REPO is set.

Crow's work budget (CHECK_BUDGET) is not modelled: our deliveries stay far below it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from typing import Any, Callable, Dict, List, Optional

from ..core.model import CheckResult

MAX_DELIVERY_BYTES = 1_000_000          # the job API's limit (cloud/src/lab.ts), under Crow's 1 MiB of ciphertext
MAX_SAFE = 2 ** 53 - 1


# ---------------------------------------------------------------------------------------------- JSON as JavaScript
def _is_obj(v) -> bool:
    return isinstance(v, dict)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _type_of(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if _is_num(v):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    return "object"


def _is_integer(v) -> bool:                 # Number.isInteger
    return _is_num(v) and math.isfinite(v) and float(v).is_integer()


def _is_safe_int(v) -> bool:                # Number.isSafeInteger
    return _is_integer(v) and abs(v) <= MAX_SAFE


def _js_equal(a, b) -> bool:
    """Equality of two parsed JSON values as Crow compares them (canonical JSON, jsonEqual): 1 equals 1.0, true is
    not 1, and objects compare by keys, not by order."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if _is_num(a) or _is_num(b):
        return _is_num(a) and _is_num(b) and a == b
    if isinstance(a, list):
        return isinstance(b, list) and len(a) == len(b) and all(_js_equal(x, y) for x, y in zip(a, b))
    if _is_obj(a):
        return _is_obj(b) and a.keys() == b.keys() and all(_js_equal(a[k], b[k]) for k in a)
    return type(a) is type(b) and a == b


_POINTER = re.compile(r"^(/([^~/]|~[01])*)*$")


def resolve(document, pointer: str):
    """The value at an RFC 6901 pointer, as a 1-tuple, or None when nothing is there."""
    if not isinstance(pointer, str) or not _POINTER.match(pointer):
        return None
    value = document
    for token in pointer.split("/")[1:]:
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", key) or int(key) >= len(value):
                return None
            value = value[int(key)]
        elif _is_obj(value) and key in value:
            value = value[key]
        else:
            return None
    return (value,)


# ---------------------------------------------------------------------------------------------- json_schema
class SchemaError(ValueError):
    pass


_ANNOTATIONS = {"$schema", "title", "description", "$comment", "examples", "default"}
_TYPES = {"null", "boolean", "object", "array", "number", "integer", "string"}
Validator = Callable[[Any], Optional[str]]


def compile_schema(schema, depth: int = 0) -> Validator:
    """Crow's json-schema.ts: the same keywords, the same order of evaluation, the same first error."""
    if depth > 32:
        raise SchemaError("schema deeper than 32")
    if schema is True:
        return lambda x: None
    if schema is False:
        return lambda x: "false schema"
    if not _is_obj(schema):
        raise SchemaError("a schema must be an object or boolean")
    sub = lambda s: compile_schema(s, depth + 1)  # noqa: E731
    checks: List[Validator] = []
    for k, v in schema.items():
        if k in _ANNOTATIONS:
            continue
        if k == "type":
            ts = v if isinstance(v, list) else [v]
            if not ts or not all(isinstance(t, str) and t in _TYPES for t in ts):
                raise SchemaError("type must name JSON types")
            names = set(ts)
            expected = "expected " + "|".join(ts)
            checks.append(lambda x, names=names, expected=expected: None if (
                _type_of(x) in names or ("integer" in names and _is_integer(x))) else expected)
        elif k == "enum":
            if not isinstance(v, list):
                raise SchemaError("enum must be an array")
            checks.append(lambda x, v=v: None if any(_js_equal(x, e) for e in v) else "not in enum")
        elif k == "const":
            checks.append(lambda x, v=v: None if _js_equal(x, v) else "not equal to const")
        elif k == "properties":
            if not _is_obj(v):
                raise SchemaError("properties must be an object")
            props = [(name, sub(s)) for name, s in v.items()]

            def _props(x, props=props):
                if not _is_obj(x):
                    return None
                for name, val in props:
                    if name in x:
                        e = val(x[name])
                        if e:
                            return f"/{name}: {e}"
                return None
            checks.append(_props)
        elif k == "required":
            if not isinstance(v, list) or not all(isinstance(r, str) for r in v):
                raise SchemaError("required must be an array of strings")
            names_r = list(dict.fromkeys(v))
            checks.append(lambda x, names_r=names_r: next(
                (f"missing {r}" for r in names_r if _is_obj(x) and r not in x), None))
        elif k == "additionalProperties":
            known = set(schema["properties"]) if _is_obj(schema.get("properties")) else set()
            val = sub(v)

            def _addl(x, known=known, val=val):
                if not _is_obj(x):
                    return None
                for name in x:
                    if name not in known:
                        e = val(x[name])
                        if e:
                            return f"/{name}: additional property {e}"
                return None
            checks.append(_addl)
        elif k == "items":
            val = sub(v)

            def _items(x, val=val):
                if not isinstance(x, list):
                    return None
                for i, item in enumerate(x):
                    e = val(item)
                    if e:
                        return f"/{i}: {e}"
                return None
            checks.append(_items)
        elif k in ("minItems", "maxItems", "minLength", "maxLength"):
            if not (_is_safe_int(v) and v >= 0):
                raise SchemaError(f"{k} must be a non-negative integer")
            n = int(v)
            if k == "minItems":
                checks.append(lambda x, n=n: f"fewer than {n} items" if isinstance(x, list) and len(x) < n else None)
            elif k == "maxItems":
                checks.append(lambda x, n=n: f"more than {n} items" if isinstance(x, list) and len(x) > n else None)
            elif k == "minLength":
                checks.append(lambda x, n=n: f"shorter than {n}" if isinstance(x, str) and len(x) < n else None)
            else:
                checks.append(lambda x, n=n: f"longer than {n}" if isinstance(x, str) and len(x) > n else None)
        elif k == "uniqueItems":
            if not isinstance(v, bool):
                raise SchemaError("uniqueItems must be boolean")
            if v:
                checks.append(lambda x: "items not unique" if isinstance(x, list) and any(
                    _js_equal(x[i], x[j]) for i in range(len(x)) for j in range(i)) else None)
        elif k in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
            if not (_is_num(v) and math.isfinite(v)):
                raise SchemaError(f"{k} must be a number")
            fmt = _num_text(v)
            test, msg = {"minimum": (lambda x, n: x < n, f"below {fmt}"),
                         "maximum": (lambda x, n: x > n, f"above {fmt}"),
                         "exclusiveMinimum": (lambda x, n: x <= n, f"not above {fmt}"),
                         "exclusiveMaximum": (lambda x, n: x >= n, f"not below {fmt}")}[k]
            checks.append(lambda x, n=v, test=test, msg=msg: msg if _is_num(x) and test(x, n) else None)
        elif k == "pattern":
            if not isinstance(v, str):
                raise SchemaError("pattern must be a string")
            try:
                rx = re.compile(v)
            except re.error as e:
                raise SchemaError(f"pattern: {e}") from None
            checks.append(lambda x, rx=rx: "does not match pattern" if isinstance(x, str) and not rx.search(x) else None)
        elif k in ("anyOf", "allOf", "oneOf"):
            if not isinstance(v, list) or not v:
                raise SchemaError(f"{k} must be a non-empty array")
            vals = [sub(s) for s in v]

            def _comb(x, vals=vals, k=k):
                passing = sum(1 for val in vals if val(x) is None)
                if k == "allOf":
                    return None if passing == len(vals) else "allOf not satisfied"
                if k == "anyOf":
                    return None if passing > 0 else "anyOf not satisfied"
                return None if passing == 1 else f"oneOf matched {passing}"
            checks.append(_comb)
        elif k == "not":
            val = sub(v)
            checks.append(lambda x, val=val: "matches not" if val(x) is None else None)
        else:
            raise SchemaError(f"keyword {k[:40]} is not supported")

    def run(x):
        for c in checks:
            e = c(x)
            if e:
                return e
        return None
    return run


def _num_text(n) -> str:
    return str(int(n)) if _is_integer(n) else repr(n)


# ---------------------------------------------------------------------------------------------- signature_valid
def _strict_b64(text: str) -> Optional[bytes]:
    if not isinstance(text, str) or len(text) % 4 or not re.fullmatch(r"[A-Za-z0-9+/]*={0,2}", text):
        return None
    try:
        raw = base64.b64decode(text, validate=True)
    except ValueError:
        return None
    return raw if base64.b64encode(raw).decode() == text else None     # one spelling per byte string


def _pem_spki(text: str) -> Optional[bytes]:
    m = re.search(r"-----BEGIN PUBLIC KEY-----\r?\n([\s\S]*?)\r?\n-----END PUBLIC KEY-----", text)
    return _strict_b64(re.sub(r"\r?\n", "", m.group(1))) if m else None


def _signature_valid(check: dict, doc) -> CheckResult:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519

    cid = check.get("id", "?")
    fail = lambda d: CheckResult(cid, False, d)  # noqa: E731
    alg, pk, msg, sig = check.get("alg"), check.get("publicKey") or {}, check.get("message") or {}, check.get("signature") or {}
    if alg not in ("ecdsa-p256-sha256", "ed25519"):
        return fail("check invalid: alg must be ecdsa-p256-sha256 or ed25519")
    strings = {}
    for name, spec in (("publicKey", pk), ("message", msg), ("signature", sig)):
        found = resolve(doc, spec.get("pointer", ""))
        if not found:
            return fail(f"{name}: nothing at the pointer")
        if not isinstance(found[0], str):
            return fail(f"{name}: not a string")
        strings[name] = found[0]
    spki = _pem_spki(strings["publicKey"]) if pk.get("format") == "spki-pem" else _strict_b64(strings["publicKey"])
    if not spki:
        return fail("publicKey: not a PEM public key" if pk.get("format") == "spki-pem" else "publicKey: not valid base64")
    if "sha256:" + hashlib.sha256(spki).hexdigest() != pk.get("spkiSha256"):
        return fail("publicKey: fingerprint differs from the terms")
    try:
        key = serialization.load_der_public_key(spki)
    except ValueError:
        return fail("publicKey: not a public key")
    if alg == "ecdsa-p256-sha256" and not (isinstance(key, ec.EllipticCurvePublicKey) and key.curve.name == "secp256r1"):
        return fail("publicKey: not a P-256 key")
    if alg == "ed25519" and not isinstance(key, ed25519.Ed25519PublicKey):
        return fail("publicKey: not an Ed25519 key")
    signature = _strict_b64(strings["signature"])
    if signature is None:
        return fail("signature: not valid base64")
    if msg.get("encoding") == "utf8":
        try:
            message = strings["message"].encode("utf-8")
        except UnicodeEncodeError:
            return fail("message: not well-formed text")
    else:
        message = _strict_b64(strings["message"])
        if message is None:
            return fail("message: not valid base64")
    try:
        if alg == "ed25519":
            key.verify(signature, message)
        elif sig.get("format") == "der":
            key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
        else:                                       # raw r||s, 64 bytes
            from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
            if len(signature) != 64:
                return fail("signature: wrong length")
            r, s = int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
            key.verify(encode_dss_signature(r, s), message, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        return fail("signature does not verify")
    if "jsonEquals" in msg:
        try:
            signed = json.loads(message.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return fail("message: not JSON")
        named = resolve(doc, msg["jsonEquals"])
        if not named or not _js_equal(signed, named[0]):
            return fail("message: differs from the value at jsonEquals")
    return CheckResult(cid, True)


# ---------------------------------------------------------------------------------------------- series
def _read_array(spec, label, doc):
    if not _is_obj(spec) or set(spec) != {"pointer"}:
        return None, f"check invalid: {label} must be exactly {{ pointer }}"
    found = resolve(doc, spec["pointer"])
    if not found:
        return None, f"{label}: nothing at the pointer"
    if not isinstance(found[0], list):
        return None, f"{label}: not an array"
    return found[0], None


def _equal_lengths(check, doc) -> CheckResult:
    cid, series = check.get("id", "?"), check.get("series")
    if not isinstance(series, list) or not 2 <= len(series) <= 32:
        return CheckResult(cid, False, "check invalid: series must list 2 to 32 pointers")
    first = 0
    for i, s in enumerate(series):
        arr, err = _read_array(s, f"series {i + 1}", doc)
        if err:
            return CheckResult(cid, False, err)
        if i == 0:
            first = len(arr)
        elif len(arr) != first:
            return CheckResult(cid, False, f"series {i + 1} has {len(arr)} entries; series 1 has {first}")
    return CheckResult(cid, True, f"{len(series)} series of {first} entries")


def _monotonic_column(check, doc) -> CheckResult:
    cid = check.get("id", "?")
    arr, err = _read_array(check.get("series"), "series", doc)     # our templates use pointers, never CSV columns
    if err:
        return CheckResult(cid, False, err)
    for i, v in enumerate(arr):
        if not _is_safe_int(v):
            return CheckResult(cid, False, f"series: row {i + 1} is not an integer")
    lo, hi = check.get("minStep", 0), check.get("maxStep")
    for i in range(1, len(arr)):
        step = arr[i] - arr[i - 1]
        if step < 0:
            return CheckResult(cid, False, f"row {i + 1} is lower than row {i}")
        if step < lo:
            return CheckResult(cid, False, f"row {i + 1} steps less than minStep after row {i}")
        if hi is not None and step > hi:
            return CheckResult(cid, False, f"row {i + 1} steps more than maxStep after row {i}")
    return CheckResult(cid, True, f"{len(arr)} rows in order")


# ---------------------------------------------------------------------------------------------- hidden characters
_HIDDEN = re.compile(
    "[\U000E0000-\U000E007F\u061C\u200E\u200F\u202A-\u202E\u2066-\u2069\uFE00-\uFE0F\U000E0100-\U000E01EF"
    "\u180B-\u180D\u180F\u00AD\u034F\u115F\u1160\u17B4\u17B5\u180E\u200B-\u200D\u2060-\u2064\u206A-\u206F"
    "\u3164\uFEFF\uFFA0\U0001D173-\U0001D17A]")
HIDDEN_KEYS = ("tag", "bidi", "variationSelector", "otherIgnorable", "total")


def _hidden_class(cp: int) -> str:
    if 0xE0000 <= cp <= 0xE007F:
        return "tag"
    if cp in (0x061C, 0x200E, 0x200F) or 0x202A <= cp <= 0x202E or 0x2066 <= cp <= 0x2069:
        return "bidi"
    if 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF or 0x180B <= cp <= 0x180D or cp == 0x180F:
        return "variationSelector"
    return "otherIgnorable"


def count_hidden(text: str) -> Dict[str, int]:
    """Crow's hidden.ts, on the text as delivered; a JSON delivery's \\u escapes are decoded first (Crow's D99)."""
    if "\\u" in text:
        text = re.sub(r"\\(?:u([0-9a-fA-F]{4})|[\s\S])", lambda m: chr(int(m.group(1), 16)) if m.group(1) else m.group(0), text)
        text = text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")    # join escaped surrogate pairs
    c = dict.fromkeys(HIDDEN_KEYS, 0)
    for m in _HIDDEN.finditer(text):
        c[_hidden_class(ord(m.group(0)))] += 1
        c["total"] += 1
    return c


def _hidden_max(check, text) -> CheckResult:
    cid, mx = check.get("id", "?"), check.get("max") or {}
    if not mx or any(k not in HIDDEN_KEYS or not (_is_safe_int(n) and n >= 0) for k, n in mx.items()):
        return CheckResult(cid, False, "check invalid: max")
    counts = count_hidden(text)
    over = [k for k in HIDDEN_KEYS if k in mx and counts[k] > mx[k]]
    summary = ", ".join(f"{k} {counts[k]}" for k in HIDDEN_KEYS)
    return CheckResult(cid, not over, (f"over the limit for {', '.join(over)} ({summary})" if over else summary))


# ---------------------------------------------------------------------------------------------- the deal's checks
def evaluate(checks: List[dict], delivery: bytes) -> List[CheckResult]:
    """Every check of the terms on the delivery bytes, plus the size and JSON intake. A result's id is the check's."""
    out = [CheckResult("intake-size", len(delivery) <= MAX_DELIVERY_BYTES, f"{len(delivery)} bytes (limit {MAX_DELIVERY_BYTES})")]
    try:
        text = delivery.decode("utf-8")
        doc = json.loads(text)
    except (UnicodeDecodeError, ValueError):
        return out + [CheckResult("intake-json", False, "not a UTF-8 JSON document")]
    for c in checks:
        cid, kind = c.get("id", "?"), c.get("type")
        try:
            if kind == "json_schema":
                e = compile_schema(c.get("schema"))(doc)
                r = CheckResult(cid, e is None, f"schema: {e}" if e else "")
            elif kind == "signature_valid":
                r = _signature_valid(c, doc)
            elif kind == "equal_lengths":
                r = _equal_lengths(c, doc)
            elif kind == "monotonic_column":
                r = _monotonic_column(c, doc)
            elif kind == "hidden_characters_max":
                r = _hidden_max(c, text)
            else:
                r = CheckResult(cid, False, f"check kind {kind!r} is not run by the lab: delivery held back")
        except SchemaError as e:
            r = CheckResult(cid, False, f"schema refused: {e}")
        out.append(r)
    return out


def blocking(checks: List[dict], results: List[CheckResult]) -> List[CheckResult]:
    """The failures that would refund the buyer: intake, and every required check."""
    required = {c.get("id") for c in checks if c.get("required", True)}
    return [r for r in results if not r.ok and (r.id.startswith("intake-") or r.id in required)]


def deal_checks(terms: dict) -> List[dict]:
    """The deterministic checks of a terms document as Crow serves it (GET /v1/commitments/{id}/terms)."""
    t = terms.get("terms", terms) if _is_obj(terms) else {}
    checks = (t.get("acceptance") or {}).get("deterministicChecks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("terms without deterministic checks")
    return checks


def accepts_simulator(checks: List[dict]) -> Optional[bool]:
    """What the deal requires of `manifest.recorder.simulator`: True (rehearsal terms: simulator only), False
    (production terms: the real robot only), or None when no required check pins it."""
    for c in checks:
        if c.get("type") != "json_schema" or not c.get("required", True):
            continue
        node = c.get("schema")
        for key in ("manifest", "recorder", "simulator"):
            node = node.get("properties", {}).get(key) if _is_obj(node) else None
        if _is_obj(node) and isinstance(node.get("const"), bool):
            return node["const"]
    return None
