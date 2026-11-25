#!/usr/bin/env python3
"""Check an order file against the lab's published limits BEFORE sending it.

Usage: python check_order.py my_order.json --limits lab_limits_cr3a.json
Exit code 0 = the lab will accept it, 1 = it will be rejected.
"""
import argparse
import json
import sys
from pathlib import Path

import orders


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("order")
    ap.add_argument("--limits", required=True, help="lab_limits_*.json published by the lab")
    args = ap.parse_args()
    try:
        order = json.loads(Path(args.order).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"Not valid JSON: {e}")
    limits = json.loads(Path(args.limits).read_text(encoding="utf-8"))
    errors, warnings = orders.validate_order(order, limits)
    for w in warnings:
        print(f"  WARNING  {w}")
    for e in errors:
        print(f"  ERROR    {e}")
    if errors:
        print("\nThe lab will REJECT this order.")
        sys.exit(1)
    path = [limits["home_deg"]] + order["waypoints"] + [limits["home_deg"]]
    print(f"Order {order['order_id']} OK: {len(order['waypoints'])} waypoints at {order['speed_factor']}% speed")
    print("Path (joint degrees J1..J6):")
    for i, p in enumerate(path):
        label = "home" if i in (0, len(path) - 1) else f"wp{i}"
        print(f"  {label:<5} {p}")


if __name__ == "__main__":
    main()
