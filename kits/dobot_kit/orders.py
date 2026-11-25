"""Order format and validation, shared by make_order.py, check_order.py, run_order.py and episode.py.
See ORDER_FORMAT.md for the human-readable specification.
"""
import datetime as dt
import math
import re

import dobot_proto as dp

ORDER_KEYS = {"order_id", "nonce", "speed_factor", "waypoints", "issued_at_utc", "notes"}
ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
NONCE_RE = re.compile(r"^[0-9a-fA-F]{12,128}$")
MAX_WAYPOINTS = 10


def parse_utc(s):
    s = s.strip().replace("Z", "+00:00")
    t = dt.datetime.fromisoformat(s)
    if t.tzinfo is None:
        raise ValueError("timestamp needs a timezone, e.g. 2026-11-18T10:00:00Z")
    return t.astimezone(dt.timezone.utc)


def check_points(points, envelope):
    errors = []
    for i, p in enumerate(points):
        for j, v in enumerate(p):
            lo = max(envelope[j][0], dp.CR3A_SPEC["joint_range_deg"][j][0])
            hi = min(envelope[j][1], dp.CR3A_SPEC["joint_range_deg"][j][1])
            if not lo <= v <= hi:
                errors.append(f"point {i}, J{j + 1} = {v} outside [{lo}, {hi}]")
    return errors


def validate_order(order, limits):
    """Return (errors, warnings). `limits` needs home_deg, max_speed_factor, allowed_envelope_deg."""
    errors, warnings = [], []
    if not isinstance(order, dict):
        return ["order must be a JSON object"], []
    for k in ("order_id", "nonce", "speed_factor", "waypoints"):
        if k not in order:
            errors.append(f"missing field '{k}'")
    for k in set(order) - ORDER_KEYS:
        warnings.append(f"unknown field '{k}' (ignored)")
    if errors:
        return errors, warnings

    if not isinstance(order["order_id"], str) or not ID_RE.match(order["order_id"]):
        errors.append("order_id: 1-64 characters among A-Z a-z 0-9 _ . -")
    if not isinstance(order["nonce"], str) or not NONCE_RE.match(order["nonce"]):
        errors.append("nonce: hexadecimal string, 12-128 characters")
    elif len(order["nonce"]) < 32:
        warnings.append("nonce shorter than 32 hex characters (128 bits): fine for tests, use 32+ for purchases")

    sp = order["speed_factor"]
    max_sp = int(limits.get("max_speed_factor", 20))
    if not isinstance(sp, int) or isinstance(sp, bool) or not 1 <= sp <= max_sp:
        errors.append(f"speed_factor: integer from 1 to {max_sp} (the lab's current maximum)")

    wps = order["waypoints"]
    if not isinstance(wps, list) or not 1 <= len(wps) <= MAX_WAYPOINTS:
        errors.append(f"waypoints: list of 1 to {MAX_WAYPOINTS} points")
    else:
        for i, p in enumerate(wps):
            if (not isinstance(p, list) or len(p) != 6
                    or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                               for v in p)):
                errors.append(f"waypoint {i}: must be a list of 6 numbers (J1..J6, degrees)")
        if not errors:
            errors += check_points([limits["home_deg"]] + wps, limits["allowed_envelope_deg"])
            prev = limits["home_deg"]
            for i, p in enumerate(wps):
                if max(abs(a - b) for a, b in zip(p, prev)) < 0.5:
                    warnings.append(f"waypoint {i} is almost identical to the previous point")
                prev = p

    if "issued_at_utc" in order:
        try:
            t = parse_utc(order["issued_at_utc"])
            if t > dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5):
                warnings.append("issued_at_utc is in the future (check the clock)")
        except (ValueError, AttributeError) as e:
            errors.append(f"issued_at_utc: ISO 8601 UTC timestamp, e.g. 2026-11-18T10:00:00Z ({e})")
    if "notes" in order and (not isinstance(order["notes"], str) or len(order["notes"]) > 500):
        errors.append("notes: string, at most 500 characters")
    return errors, warnings
