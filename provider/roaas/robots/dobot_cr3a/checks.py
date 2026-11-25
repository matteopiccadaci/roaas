"""CR3A data checks, shared by the recorder (summary signed in the manifest) and the verifier.

The rules are the kit's verify_episode.py (v0.4.5) rules, written as functions over columns:
datasheet limits (+2% speed margin), continuity (max speed x dt x 1.2 + 0.05 deg), envelope (+/- tol),
strictly increasing controller timestamps, and every target reached in order within `tol` degrees.
"""
from __future__ import annotations

import base64
import csv
import datetime as dt
import gzip
import json
from pathlib import Path
from typing import List, Optional, Sequence

from ...core.canonical import canonical, sha256_file, sha256_hex
from ...core.model import CheckResult
from .kit import dobot_proto as dp

SPEC = dp.CR3A_SPEC
TOL_DEG = 0.5


def _err(row: Sequence[float], target: Sequence[float]) -> float:
    return max(abs(a - b) for a, b in zip(row, target))


def arrivals(q: List[List[float]], targets: List[List[float]], tol: float = TOL_DEG) -> List[dict]:
    """For each target in order: the sample of closest approach within the first dwell inside `tol`."""
    out, idx = [], 0
    for n, wp in enumerate(targets):
        found = next((k for k in range(idx, len(q)) if _err(q[k], wp) <= tol), None)
        if found is None:
            rest = [_err(q[k], wp) for k in range(idx, len(q))]
            out.append({"point": n, "target_deg": wp, "reached": False, "sample": None,
                        "max_error_deg": round(min(rest), 4) if rest else None})
            for m in range(n + 1, len(targets)):
                out.append({"point": m, "target_deg": targets[m], "reached": False, "sample": None,
                            "max_error_deg": None})
            break
        k, best, best_k = found, _err(q[found], wp), found
        while k < len(q) and _err(q[k], wp) <= tol:
            e = _err(q[k], wp)
            if e < best:
                best, best_k = e, k
            k += 1
        out.append({"point": n, "target_deg": wp, "reached": True, "sample": best_k,
                    "max_error_deg": round(best, 4)})
        idx = k
    return out


def spec_violations(q, qd) -> int:
    bad = 0
    for k in range(len(q)):
        for j in range(6):
            lo, hi = SPEC["joint_range_deg"][j]
            if not lo <= q[k][j] <= hi or abs(qd[k][j]) > SPEC["joint_speed_max_deg_s"][j] * 1.02:
                bad += 1
    return bad


def continuity_violations(q, ts_ms) -> int:
    bad = 0
    for k in range(len(q) - 1):
        dt_s = max((ts_ms[k + 1] - ts_ms[k]) / 1000, 0.001)
        for j in range(6):
            if abs(q[k + 1][j] - q[k][j]) > SPEC["joint_speed_max_deg_s"][j] * dt_s * 1.2 + 0.05:
                bad += 1
    return bad


def envelope_violations(q, envelope, tol: float = TOL_DEG) -> int:
    return sum(1 for row in q for j in range(6) if not envelope[j][0] - tol <= row[j] <= envelope[j][1] + tol)


def timestamps_increasing(ts_ms) -> bool:
    return all(b > a for a, b in zip(ts_ms, ts_ms[1:]))


def read_telemetry(path: Path):
    with gzip.open(path, "rt") as f:
        rows = list(csv.DictReader(f))
    q = [[float(r[f"q{j}"]) for j in range(1, 7)] for r in rows]
    qd = [[float(r[f"qd{j}"]) for j in range(1, 7)] for r in rows]
    ts = [int(r["t_controller_ms"]) for r in rows]
    return rows, q, qd, ts


def verify_episode_dir(ep: Path, order_raw: Optional[bytes] = None, fingerprint: Optional[str] = None,
                       tol: float = TOL_DEG) -> List[CheckResult]:
    """Every check of the kit's verify_episode.py, plus the v0.6 additions (targets, summary, preview hash)."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    res: List[CheckResult] = []
    body = (ep / "manifest.json").read_bytes()
    pub = serialization.load_pem_public_key((ep / "pubkey.pem").read_bytes())
    try:
        pub.verify(base64.b64decode((ep / "manifest.sig").read_text()), body, ec.ECDSA(hashes.SHA256()))
        res.append(CheckResult("signature", True))
    except InvalidSignature:
        return [CheckResult("signature", False, "manifest signature does not verify")]
    m = json.loads(body)
    fp = sha256_hex(pub.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo))
    res.append(CheckResult("manifest-names-signer", m.get("signer", {}).get("pubkey_sha256") == fp))
    if fingerprint:
        res.append(CheckResult("fingerprint", fp == fingerprint.lower().strip(), f"key {fp}"))
    for name, meta in m["files"].items():
        res.append(CheckResult(f"hash:{name}", sha256_file(ep / name) == meta["sha256"]))
    res.append(CheckResult("status", m.get("status") == "completed", m.get("abort_reason") or ""))
    if order_raw is not None:
        res.append(CheckResult("order-bytes", sha256_hex(order_raw) == m["order"]["order_sha256"]))
    issued = m["order"].get("issued_at_utc")
    if issued and m.get("recorded_at_utc_iso"):
        t_i = dt.datetime.fromisoformat(issued.replace("Z", "+00:00"))
        t_r = dt.datetime.fromisoformat(m["recorded_at_utc_iso"].replace("Z", "+00:00"))
        res.append(CheckResult("recorded-after-issued", t_r >= t_i))

    rows, q, qd, ts = read_telemetry(ep / "telemetry.csv.gz")
    res.append(CheckResult("sample-count", len(rows) == m["stats"]["samples"], f"{len(rows)} samples"))
    if not m["program"].get("record_only"):
        targets = m["program"].get("targets_deg") or (
            [m["program"]["home_deg"]] + m["order"]["waypoints_deg"] + [m["program"]["home_deg"]])
        arr = arrivals(q, targets, tol)
        missed = [a["point"] for a in arr if not a["reached"]]
        res.append(CheckResult("targets-reached", not missed, f"{len(targets)} targets" + (f"; missed {missed}" if missed else "")))
        env = m["program"].get("allowed_envelope_deg")
        if env:
            n = envelope_violations(q, env, tol)
            res.append(CheckResult("envelope", n == 0, f"{n} values outside"))
        if "summary" in m:
            declared = [(a["point"], a["sample"]) for a in m["summary"]["arrivals"]]
            res.append(CheckResult("summary-arrivals-match", declared == [(a["point"], a["sample"]) for a in arr]))
    n = spec_violations(q, qd)
    res.append(CheckResult("datasheet-limits", n == 0, f"{n} values outside"))
    res.append(CheckResult("timestamps-increasing", timestamps_increasing(ts)))
    n = continuity_violations(q, ts)
    res.append(CheckResult("continuity", n == 0, f"{n} jumps"))
    if "preview" in m:
        pv = m["preview"]
        again = preview_series(q, qd, ts, pv["step"], pv.get("source_samples", ()))
        res.append(CheckResult("preview-matches-telemetry", again == pv,
                               f"{pv['samples']} samples at {pv['rate_hz']} Hz"))
    return res


def preview_series(q, qd, ts_ms, step: int, keep=()) -> dict:
    """The checkable preview: every `step`-th sample plus the samples in `keep` (the arrivals) and the last one,
    as INTEGER series, one per axis (Crow's series checks read integers only): time in ms from the first sample,
    positions in millidegrees, speeds in millidegrees per second."""
    n = len(ts_ms)
    idx = sorted(set(range(0, n, step)) | {n - 1} | {k for k in keep if k is not None and 0 <= k < n})
    t0 = ts_ms[0] if n else 0
    p = {"rate_hz": round(125 / step, 3), "step": step, "samples": len(idx), "t_ms": [ts_ms[k] - t0 for k in idx]}
    for j in range(6):
        p[f"q{j + 1}_mdeg"] = [int(round(q[k][j] * 1000)) for k in idx]
    for j in range(6):
        p[f"qd{j + 1}_mdeg_s"] = [int(round(qd[k][j] * 1000)) for k in idx]
    p["source_samples"] = idx
    return p


SERIES = ["t_ms"] + [f"q{j}_mdeg" for j in range(1, 7)] + [f"qd{j}_mdeg_s" for j in range(1, 7)]
PREVIEW_STEPS = (1, 2, 5, 12, 25)      # 125, 62.5, 25, ~10.4, 5 Hz


def choose_preview(q, qd, ts_ms, budget_bytes: int, keep=()) -> dict:
    """The densest preview whose canonical JSON fits in `budget_bytes` (it travels twice in a delivery: as the
    signed text and as the object the checks read)."""
    for step in PREVIEW_STEPS:
        p = preview_series(q, qd, ts_ms, step, keep)
        if len(canonical(p)) <= budget_bytes:
            return p
    raise ValueError(f"even a 5 Hz preview exceeds {budget_bytes} bytes: the recording is too long")
