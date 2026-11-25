"""Task schema `roaas-task/1`, plus the v0.4.x CR3A order file accepted as is (backwards compatible).

New format:
  {"schema": "roaas-task/1", "robot": "dobot_cr3a", "task": "joint_waypoints",
   "order_id": "...", "nonce": "<hex>", "issued_at_utc": "...", "params": {...}, "notes": "..."}

Legacy CR3A order (no "schema"): {"order_id", "nonce", "speed_factor", "waypoints", "issued_at_utc"?, "notes"?}
is read as robot "dobot_cr3a", task "joint_waypoints", params {speed_factor, waypoints}.

The order's exact bytes are kept in Task.raw: they are what the manifest hashes and what the buyer compares.
This module checks only the generic fields; each robot adapter validates `params`.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from typing import List, Tuple

from .model import Task

SCHEMA = "roaas-task/1"
ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")          # same rule as the kit's orders.py
NONCE_RE = re.compile(r"^[0-9a-fA-F]{12,128}$")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
TASK_KEYS = {"schema", "robot", "task", "order_id", "nonce", "issued_at_utc", "params", "notes"}
LEGACY_KEYS = {"order_id", "nonce", "speed_factor", "waypoints", "issued_at_utc", "notes"}
MAX_ORDER_BYTES = 16_384


class TaskError(ValueError):
    def __init__(self, errors: List[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def parse_utc(s: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError("timestamp needs a timezone, e.g. 2026-11-18T10:00:00Z")
    return t.astimezone(dt.timezone.utc)


def parse_task(raw: bytes) -> Tuple[Task, List[str]]:
    """Parse an order file (new or legacy format). Returns (task, warnings); raises TaskError on bad input."""
    if len(raw) > MAX_ORDER_BYTES:
        raise TaskError([f"order is {len(raw)} bytes; at most {MAX_ORDER_BYTES}"])
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise TaskError([f"order is not UTF-8 JSON: {e}"]) from None
    if not isinstance(obj, dict):
        raise TaskError(["order must be a JSON object"])

    errors: List[str] = []
    warnings: List[str] = []
    legacy = "schema" not in obj
    if legacy:
        for k in ("order_id", "nonce", "speed_factor", "waypoints"):
            if k not in obj:
                errors.append(f"missing field '{k}'")
        warnings += [f"unknown field '{k}' (ignored)" for k in sorted(set(obj) - LEGACY_KEYS)]
        robot, task_type = "dobot_cr3a", "joint_waypoints"
        params = {"speed_factor": obj.get("speed_factor"), "waypoints": obj.get("waypoints")}
    else:
        if obj.get("schema") != SCHEMA:
            errors.append(f"schema must be '{SCHEMA}'")
        for k in ("robot", "task", "order_id", "nonce", "params"):
            if k not in obj:
                errors.append(f"missing field '{k}'")
        warnings += [f"unknown field '{k}' (ignored)" for k in sorted(set(obj) - TASK_KEYS)]
        robot, task_type, params = obj.get("robot"), obj.get("task"), obj.get("params")
        for name, v in (("robot", robot), ("task", task_type)):
            if name in obj and (not isinstance(v, str) or not NAME_RE.match(v)):
                errors.append(f"{name}: lowercase letters, digits and _, starting with a letter")
        if "params" in obj and not isinstance(params, dict):
            errors.append("params must be an object")
    if errors:
        raise TaskError(errors)

    order_id, nonce = obj["order_id"], obj["nonce"]
    if not isinstance(order_id, str) or not ID_RE.match(order_id):
        errors.append("order_id: 1-64 characters among A-Z a-z 0-9 _ . -")
    if not isinstance(nonce, str) or not NONCE_RE.match(nonce):
        errors.append("nonce: hexadecimal string, 12-128 characters")
    elif len(nonce) < 32:
        warnings.append("nonce shorter than 32 hex characters (128 bits): fine for tests, use 32+ for purchases")
    issued = obj.get("issued_at_utc")
    if issued is not None:
        try:
            if parse_utc(issued) > dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5):
                warnings.append("issued_at_utc is in the future (check the clock)")
        except (ValueError, AttributeError, TypeError) as e:
            errors.append(f"issued_at_utc: ISO 8601 UTC timestamp, e.g. 2026-11-18T10:00:00Z ({e})")
    notes = obj.get("notes")
    if notes is not None and (not isinstance(notes, str) or len(notes) > 500):
        errors.append("notes: string, at most 500 characters")
    if errors:
        raise TaskError(errors)
    return Task(robot=robot, task=task_type, order_id=order_id, nonce=nonce, params=params, raw=raw,
                issued_at_utc=issued, notes=notes, legacy=legacy), warnings


def encode_task(robot: str, task: str, order_id: str, nonce: str, params: dict,
                issued_at_utc: str = None, notes: str = None) -> bytes:
    """A new-format order as compact ASCII JSON: suitable for Crow's `scope.order` string field."""
    obj = {"schema": SCHEMA, "robot": robot, "task": task, "order_id": order_id, "nonce": nonce,
           "params": params}
    if issued_at_utc:
        obj["issued_at_utc"] = issued_at_utc
    if notes:
        obj["notes"] = notes
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=True).encode()
