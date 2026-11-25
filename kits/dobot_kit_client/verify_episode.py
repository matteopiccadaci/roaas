#!/usr/bin/env python3
"""Verify an episode the way a buyer would.

Checks: manifest signature, file hashes, expected nonce, that the trajectory reaches
the ordered points in the right order, and that every value respects the datasheet limits.

Usage:
  python verify_episode.py <episode folder> --order my_order.json --fingerprint <expected key sha256>
  (or --nonce CODE instead of --order, --pubkey key.pem instead of --fingerprint)

Without --fingerprint / --pubkey the key shipped in the folder is trusted: fine for tests only.
"""
import argparse
import base64
import csv
import gzip
import hashlib
import json
import sys
from pathlib import Path

import dobot_proto as dp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("--nonce", help="random code expected in the order")
    ap.add_argument("--order", help="your copy of the order JSON: checks its exact bytes, nonce and waypoints")
    ap.add_argument("--fingerprint", help="expected SHA-256 of the signer's public key (from the lab, out of band)")
    ap.add_argument("--pubkey", help="expected public key (if omitted, the one in the folder is used: test only)")
    ap.add_argument("--tol-deg", type=float, default=0.5, help="tolerance to consider a point reached")
    args = ap.parse_args()

    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    ep = Path(args.episode)
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("  OK   " if cond else "  FAIL ") + msg)
        ok &= bool(cond)

    body = (ep / "manifest.json").read_bytes()
    sig = base64.b64decode((ep / "manifest.sig").read_text())
    pub_path = Path(args.pubkey) if args.pubkey else ep / "pubkey.pem"
    pub = serialization.load_pem_public_key(pub_path.read_bytes())
    try:
        pub.verify(sig, body, ec.ECDSA(hashes.SHA256()))
        check(True, f"valid signature (key {pub_path})")
    except InvalidSignature:
        check(False, "signature NOT valid")
        sys.exit(1)

    m = json.loads(body)
    import hashlib as _h
    fp = _h.sha256(pub.public_bytes(serialization.Encoding.DER,
                                    serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()
    signer = m.get("signer", {})
    print(f"  --   signer: {signer.get('type', '?')}  key sha256 {fp}")
    if args.fingerprint:
        check(fp == args.fingerprint.lower().strip(), "signer key matches the expected fingerprint")
    elif not args.pubkey:
        print("  NOTE no --fingerprint/--pubkey given: the key in the folder is trusted (test only)")
    if signer.get("pubkey_sha256"):
        check(signer["pubkey_sha256"] == fp, "manifest names the same key that signed it")
    if (ep / "signer_cert.cer").exists():
        from cryptography import x509
        cert = x509.load_der_x509_certificate((ep / "signer_cert.cer").read_bytes())
        cfp = _h.sha256(cert.public_key().public_bytes(serialization.Encoding.DER,
                                                       serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()
        check(cfp == fp, f"signer certificate holds the same key ({cert.subject.rfc4514_string()})")
    for name, meta in m["files"].items():
        h = hashlib.sha256((ep / name).read_bytes()).hexdigest()
        check(h == meta["sha256"], f"hash of {name}")
    if (ep / "raw.bin").exists():
        h = hashlib.sha256((ep / "raw.bin").read_bytes()).hexdigest()
        check(h == m["raw_recording"]["sha256"], "hash of raw.bin (only available at the lab)")

    check(m.get("status", "completed") == "completed",
          f"run status: {m.get('status', 'completed')}" + (f" ({m['abort_reason']})" if m.get("abort_reason") else ""))
    if m["stats"].get("resyncs"):
        print(f"  NOTE feedback stream resynced {m['stats']['resyncs']} times")
    if args.nonce:
        check(m["order"]["nonce"] == args.nonce, "nonce matches the order")
    if args.order:
        mine = Path(args.order).read_bytes()
        check(hashlib.sha256(mine).hexdigest() == m["order"]["order_sha256"],
              "order executed is byte-identical to your order file")
        o = json.loads(mine)
        check(o["nonce"] == m["order"]["nonce"] and o["waypoints"] == m["order"]["waypoints_deg"],
              "nonce and waypoints match your order")
    issued = m["order"].get("issued_at_utc")
    if issued and m.get("recorded_at_utc_iso"):
        import datetime as _dt
        t_issued = _dt.datetime.fromisoformat(issued.replace("Z", "+00:00"))
        t_rec = _dt.datetime.fromisoformat(m["recorded_at_utc_iso"].replace("Z", "+00:00"))
        check(t_rec >= t_issued, f"recorded ({m['recorded_at_utc_iso']}) after the order was issued ({issued})")

    with gzip.open(ep / "telemetry.csv.gz", "rt") as f:
        rows = list(csv.DictReader(f))
    check(len(rows) == m["stats"]["samples"], f"{len(rows)} samples as declared")

    q = [[float(r[f"q{j}"]) for j in range(1, 7)] for r in rows]
    qd = [[float(r[f"qd{j}"]) for j in range(1, 7)] for r in rows]
    if m["program"].get("record_only"):
        print("  --   record-only episode: waypoint check skipped")
    else:
        targets = [m["program"]["home_deg"]] + m["order"]["waypoints_deg"] + [m["program"]["home_deg"]]
        idx = 0
        for n, wp in enumerate(targets):
            found = None
            for k in range(idx, len(q)):
                if max(abs(a - b) for a, b in zip(q[k], wp)) <= args.tol_deg:
                    found = k
                    break
            check(found is not None, f"point {n} {wp} reached" + (f" at sample {found}" if found is not None else ""))
            if found is None:
                break
            # skip the dwell on this point, so the next match is really later
            idx = found
            while idx < len(q) and max(abs(a - b) for a, b in zip(q[idx], wp)) <= args.tol_deg:
                idx += 1

    spec = dp.CR3A_SPEC
    bad = 0
    for k in range(len(rows)):
        for j in range(6):
            lo, hi = spec["joint_range_deg"][j]
            if not lo <= q[k][j] <= hi or abs(qd[k][j]) > spec["joint_speed_max_deg_s"][j] * 1.02:
                bad += 1
    check(bad == 0, f"datasheet limits respected ({bad} values outside)")

    ts = [int(r["t_controller_ms"]) for r in rows]
    check(all(b > a for a, b in zip(ts, ts[1:])), "controller timestamps strictly increasing")

    # consistency: no joint can jump farther between two samples than its max speed allows
    jumps = []
    for k in range(len(rows) - 1):
        dt_s = max((ts[k + 1] - ts[k]) / 1000, 0.001)
        for j in range(6):
            if abs(q[k + 1][j] - q[k][j]) > spec["joint_speed_max_deg_s"][j] * dt_s * 1.2 + 0.05:
                jumps.append(f"J{j + 1} at sample {k + 1}")
    check(not jumps, "positions continuous (no physically impossible jumps)"
          + (f": {len(jumps)} found, first {jumps[:3]}" if jumps else ""))

    env = m["program"].get("allowed_envelope_deg")
    if env and not m["program"].get("record_only"):
        out = [f"J{j + 1}={q[k][j]:.2f} at sample {k}" for k in range(len(q)) for j in range(6)
               if not env[j][0] - args.tol_deg <= q[k][j] <= env[j][1] + args.tol_deg]
        check(not out, "positions inside the allowed envelope"
              + (f": {len(out)} outside, first {out[:3]}" if out else ""))

    print("\nRESULT:", "episode VALID" if ok else "episode NOT valid")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
