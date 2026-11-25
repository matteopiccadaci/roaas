#!/usr/bin/env python3
"""Generate a random order (fresh nonce + random waypoints inside the allowed envelope).

This is what a buyer does before each purchase: pick waypoints nobody could know in advance
and a random code, so the episode recorded afterwards is provably new.

Usage:
  python make_order.py --limits lab_limits_cr3a.json --points 3 --speed 30 --out my_order.json
"""
import argparse
import datetime as dt
import json
import random
import secrets
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limits", "--robot-info", dest="limits", required=True,
                    help="lab_limits_*.json published by the lab (or the lab's robot_info file)")
    ap.add_argument("--points", type=int, default=3)
    ap.add_argument("--speed", type=int, default=30, help="speed_factor (must be <= max_speed_factor)")
    ap.add_argument("--margin-deg", type=float, default=5, help="stay this far inside the envelope")
    ap.add_argument("--min-step-deg", type=float, default=10,
                    help="each waypoint differs from the previous one by at least this much on some joint")
    ap.add_argument("--out", default="order_random.json")
    args = ap.parse_args()

    info = json.loads(Path(args.limits).read_text(encoding="utf-8"))
    env = info["allowed_envelope_deg"]
    if args.speed > int(info.get("max_speed_factor", 20)):
        raise SystemExit(f"speed {args.speed} > max_speed_factor {info.get('max_speed_factor')}")
    rng = random.SystemRandom()
    prev = info["home_deg"]
    points = []
    while len(points) < args.points:
        p = [round(rng.uniform(lo + args.margin_deg, hi - args.margin_deg), 1) for lo, hi in env]
        if max(abs(a - b) for a, b in zip(p, prev)) >= args.min_step_deg:
            points.append(p)
            prev = p
    order = {"order_id": f"order-{secrets.token_hex(4)}", "nonce": secrets.token_hex(16),
             "issued_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "speed_factor": args.speed, "waypoints": points}
    Path(args.out).write_text(json.dumps(order, indent=2))
    print(json.dumps(order, indent=2))
    print(f"\nwritten to {args.out}")


if __name__ == "__main__":
    main()
