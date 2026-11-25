"""Data types shared by the core and the robot adapters."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Task:
    """One unit of robot work, independent of how it was ordered (Crow or manual)."""
    robot: str                     # robot adapter id, e.g. "dobot_cr3a"
    task: str                      # task type, e.g. "joint_waypoints"
    order_id: str
    nonce: str
    params: Dict[str, Any]
    raw: bytes                     # the order exactly as received: hashed, stored and signed
    issued_at_utc: Optional[str] = None
    notes: Optional[str] = None
    legacy: bool = False           # a v0.4.x CR3A order file (no "schema")


@dataclass
class Validation:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class Estimate:
    motion_s: float                # expected motion time on the real robot
    motion_upper_s: float          # conservative upper bound (used for timeouts and size)
    recording_upper_s: float       # motion_upper_s plus enable, pre-roll and post-roll
    segments: int


@dataclass(frozen=True)
class CheckResult:
    id: str
    ok: bool
    detail: str = ""

    def line(self) -> str:
        return ("  OK   " if self.ok else "  FAIL ") + self.id + (f": {self.detail}" if self.detail else "")


@dataclass(frozen=True)
class RobotStatus:
    reachable: bool
    ready: bool                    # may take a job now (D16)
    reason: str = ""
    mode: Optional[int] = None
    alarms: str = ""
    tcp_mode: Optional[bool] = None


@dataclass(frozen=True)
class Deal:
    """Settlement context bound into the signed manifest (decision D8)."""
    settlement: str                # "crow" or "manual"
    commitment_id: Optional[str] = None
    buyer: Optional[str] = None
    job_id: Optional[str] = None

    def as_manifest(self) -> Dict[str, Any]:
        return {"settlement": self.settlement, "commitment_id": self.commitment_id,
                "buyer": self.buyer, "job_id": self.job_id}


@dataclass(frozen=True)
class Episode:
    path: Path                     # the episode folder (manifest.json, manifest.sig, telemetry.csv.gz, ...)
    status: str                    # "completed" or "aborted"
    abort_reason: Optional[str] = None


class NotReady(RuntimeError):
    """The robot is not in a state where a job may start (nothing was sent)."""


class Cancelled(RuntimeError):
    """The operator declined: no motion command was sent."""


class Declined(RuntimeError):
    """The operator refused the job for good: nothing moved, and the job will not be run."""
