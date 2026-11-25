#!/usr/bin/env python3
"""LAB SIDE: run a buyer's order end to end and produce the delivery zip.

  1. validates the order against robot_info (envelope, speed, format)
  2. runs it on the robot with episode.py (prints the program, operator types ENABLE)
  3. verifies the episode with verify_episode.py
  4. writes deliveries/<order_id>_<time>.zip with everything the buyer needs
     (telemetry, manifest, signature, public key, the order as received; NOT raw.bin)

Usage:
  python run_order.py --order buyer_order.json --robot-info robot_info_cr3a.json --host 192.168.201.1 --live
  python run_order.py --order example_order.json --robot-info robot_info_example.json      (fake controller)
Add --signer tpm to sign with the TPM key.
"""
import argparse
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import orders

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--order", required=True)
    ap.add_argument("--robot-info", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--signer", choices=["software", "tpm"], default="software")
    ap.add_argument("--keys-dir", default="keys")
    ap.add_argument("--out", default="episodes")
    ap.add_argument("--deliveries", default="deliveries")
    args = ap.parse_args()

    order = json.loads(Path(args.order).read_text(encoding="utf-8"))
    info = json.loads(Path(args.robot_info).read_text(encoding="utf-8"))
    errors, warnings = orders.validate_order(order, info)
    for w in warnings:
        print(f"[order] warning: {w}")
    if errors:
        sys.exit("[order] REJECTED:\n  " + "\n  ".join(errors))
    print(f"[order] {order['order_id']} accepted")

    before = set(Path(args.out).glob("*")) if Path(args.out).exists() else set()
    cmd = [sys.executable, str(HERE / "episode.py"), "--host", args.host, "--order", args.order,
           "--robot-info", args.robot_info, "--out", args.out, "--signer", args.signer,
           "--keys-dir", args.keys_dir]
    if args.live:
        cmd.append("--live")
    rc = subprocess.call(cmd)
    new = sorted(set(Path(args.out).glob("*")) - before, key=lambda p: p.stat().st_mtime)
    if not new:
        sys.exit("[run] no episode folder was created")
    ep = new[-1]
    if rc != 0:
        print(f"[run] the run did not complete (exit code {rc}); episode kept for diagnosis: {ep}")
    if not (ep / "manifest.json").exists():
        sys.exit("[run] no manifest was written (the recorder never started?): nothing to verify or deliver.\n"
                 "      Is the robot (or fake_dobot.py) reachable at the given --host?")

    print("\n[verify]")
    vrc = subprocess.call([sys.executable, str(HERE / "verify_episode.py"), str(ep), "--order", args.order])
    if rc != 0 or vrc != 0:
        sys.exit("[deliver] NOT packaged: the episode is not valid")

    manifest = json.loads((ep / "manifest.json").read_text(encoding="utf-8"))
    Path(args.deliveries).mkdir(exist_ok=True)
    zpath = Path(args.deliveries) / f"{ep.name}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ["manifest.json", "manifest.sig", *manifest["files"].keys()]:
            z.write(ep / name, f"{ep.name}/{name}")
    print(f"\n[deliver] {zpath}  ({zpath.stat().st_size / 1024:.0f} KB)")
    print(f"          signer key sha256: {manifest['signer']['pubkey_sha256']}")
    print("          raw.bin stays here; its hash is in the manifest.")


if __name__ == "__main__":
    main()
