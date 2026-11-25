"""The interface every robot adapter implements (docs/ARCHITECTURE.md §3.2).

"Pure" methods never open a socket: the job API imports them to validate and price a quote.
"Lab" methods talk to the robot and run only on the lab PC.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Protocol

from ..core.approval import Approval
from ..core.model import CheckResult, Deal, Episode, Estimate, RobotStatus, Task, Validation


class TaskType(Protocol):
    name: str
    description: str


class RobotAdapter(Protocol):
    robot_id: str
    model: str

    # ---- pure
    def task_types(self) -> Dict[str, str]:
        """Task type name -> one-line description."""

    def public_limits(self) -> dict:
        """The limits the lab publishes (and buyers check against)."""

    def validate(self, task: Task, limits: Optional[dict] = None) -> Validation:
        """Errors and warnings against `limits` (default: the public ones). Never clamps."""

    def estimate(self, task: Task) -> Estimate: ...

    def program(self, task: Task, limits: Optional[dict] = None) -> List[list]:
        """The robot-level program (for an arm: the joint targets in order, home included)."""

    def plan_text(self, task: Task, limits: Optional[dict] = None) -> str:
        """The program as the operator reads it before approving."""

    def check_bounds(self, limits: Optional[dict] = None) -> Dict[str, List[int]]:
        """Integer [min, max] per preview column, for Crow's json_schema checks."""

    def verify_preview(self, preview: dict, manifest: dict) -> List[CheckResult]:
        """Checks on the delivered preview alone (what Crow's adjudicators can also read)."""

    def verify_episode_dir(self, episode_dir: Path, order_raw: Optional[bytes]) -> List[CheckResult]:
        """Every robot-specific check on a full episode folder (full telemetry)."""

    # ---- lab
    def probe(self, host: str, **ports) -> RobotStatus: ...

    def execute(self, task: Task, *, host: str, robot_info: dict, approval: Approval, signer,
                out_dir: Path, deal: Deal, **options) -> Episode: ...
