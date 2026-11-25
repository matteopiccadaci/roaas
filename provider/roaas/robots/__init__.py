"""Robot adapters and their registry. Adding a robot = one subpackage + one line here (docs/ARCHITECTURE.md §8)."""
from __future__ import annotations

from typing import Dict

from .base import RobotAdapter

_FACTORIES = {
    "dobot_cr3a": lambda: __import__("roaas.robots.dobot_cr3a.adapter", fromlist=["DobotCR3A"]).DobotCR3A(),
}
_CACHE: Dict[str, RobotAdapter] = {}


def get_adapter(robot_id: str) -> RobotAdapter:
    if robot_id not in _FACTORIES:
        raise KeyError(f"unknown robot '{robot_id}' (known: {', '.join(sorted(_FACTORIES))})")
    if robot_id not in _CACHE:
        _CACHE[robot_id] = _FACTORIES[robot_id]()
    return _CACHE[robot_id]


def known_robots():
    return sorted(_FACTORIES)
