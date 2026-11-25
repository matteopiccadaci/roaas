"""DOBOT CR3A robot adapter (first robot of ROaaS).

Task types
  joint_waypoints  params {speed_factor, waypoints[1..10]}            program: home, waypoints, home
  repeatability    params {speed_factor, waypoints[1..5], repetitions}  program: home, (waypoints, home) x N

Validation reuses the kit's orders.validate_order, so buyers' check_order.py and the lab agree exactly.
Out-of-limits tasks are rejected, never clamped. On the lab PC, `execute` validates again against the lab's
private robot_info (local limits), whatever the cloud said.
"""
from __future__ import annotations

import json
import math
import socket
from pathlib import Path
from typing import Dict, List, Optional

from ...core.model import CheckResult, Deal, Episode, Estimate, RobotStatus, Task, Validation
from . import checks, episode
from .kit import dobot_proto as dp
from .kit import orders

PUBLIC_LIMITS = json.loads((Path(__file__).parent / "public_limits.json").read_text(encoding="utf-8"))

# ROaaS placeholders (docs/ARCHITECTURE.md §5): to be set by the lab.
MAX_RECORDING_S = 600
MAX_REPETITIONS = 10
MAX_REPEAT_WAYPOINTS = 5

# Duration model, calibrated on the real CR3A (example_order.json at 30%: 8.9 s of motion, 2026-11-25).
# Per segment, the slowest joint at peak speed takes D = |dq| / (vmax * speed). Real arm: D + 0.32 s.
# Upper bound: the minimum-jerk profile of fake_dobot.py, 1.875 D, plus 0.5 s per segment.
SEGMENT_OVERHEAD_S, UPPER_FACTOR, UPPER_OVERHEAD_S = 0.32, 1.875, 0.5
RECORDING_OVERHEAD_S = 3.5          # pre-roll, enable, settle, stop and disable, post-roll

TASKS = {
    "joint_waypoints": "home, then 1 to 10 joint-space waypoints in order (MovJ, stop at each), then home; "
                       "125 Hz controller telemetry, signed",
    "repeatability": "the same path of 1 to 5 waypoints N times, back home after each repetition; "
                     "125 Hz controller telemetry, signed",
}


class DobotCR3A:
    robot_id = "dobot_cr3a"
    model = "DOBOT CR3A"

    # ------------------------------------------------------------------ pure
    def task_types(self) -> Dict[str, str]:
        return dict(TASKS)

    def public_limits(self) -> dict:
        return dict(PUBLIC_LIMITS)

    def _legacy_view(self, task: Task, waypoints) -> dict:
        o = {"order_id": task.order_id, "nonce": task.nonce,
             "speed_factor": task.params.get("speed_factor"), "waypoints": waypoints}
        if task.issued_at_utc is not None:
            o["issued_at_utc"] = task.issued_at_utc
        if task.notes is not None:
            o["notes"] = task.notes
        return o

    def validate(self, task: Task, limits: Optional[dict] = None) -> Validation:
        limits = limits or PUBLIC_LIMITS
        v = Validation()
        if task.robot != self.robot_id:
            v.errors.append(f"robot '{task.robot}' is not {self.robot_id}")
            return v
        if task.task not in TASKS:
            v.errors.append(f"task '{task.task}' unknown for {self.robot_id} (known: {', '.join(TASKS)})")
            return v
        p = task.params
        allowed = {"speed_factor", "waypoints"} | ({"repetitions"} if task.task == "repeatability" else set())
        v.warnings += [f"params: unknown field '{k}' (ignored)" for k in sorted(set(p) - allowed)]
        if task.task == "repeatability":
            reps = p.get("repetitions")
            if not isinstance(reps, int) or isinstance(reps, bool) or not 2 <= reps <= MAX_REPETITIONS:
                v.errors.append(f"repetitions: integer from 2 to {MAX_REPETITIONS}")
            wps = p.get("waypoints")
            if isinstance(wps, list) and len(wps) > MAX_REPEAT_WAYPOINTS:
                v.errors.append(f"waypoints: at most {MAX_REPEAT_WAYPOINTS} for a repeatability set")
        errs, warns = orders.validate_order(self._legacy_view(task, p.get("waypoints")), limits)
        v.errors += [e for e in errs if not e.startswith(("order_id", "nonce", "issued_at_utc", "notes"))]
        v.warnings += [w for w in warns if "nonce shorter" not in w and "issued_at_utc" not in w]
        if v.ok:
            est = self.estimate(task, limits)
            if est.recording_upper_s > MAX_RECORDING_S:
                v.errors.append(f"estimated recording up to {est.recording_upper_s:.0f} s exceeds the maximum "
                                f"{MAX_RECORDING_S} s: fewer waypoints or repetitions, or a higher speed_factor")
        return v

    def program(self, task: Task, limits: Optional[dict] = None) -> List[list]:
        home = (limits or PUBLIC_LIMITS)["home_deg"]
        wps = task.params["waypoints"]
        if task.task == "repeatability":
            return [home] + (wps + [home]) * task.params["repetitions"]
        return [home] + wps + [home]

    def estimate(self, task: Task, limits: Optional[dict] = None) -> Estimate:
        prog = self.program(task, limits)
        speed = task.params["speed_factor"] / 100
        vmax = dp.CR3A_SPEC["joint_speed_max_deg_s"]
        peak = [max(abs(b[j] - a[j]) / (vmax[j] * speed) for j in range(6)) for a, b in zip(prog, prog[1:])]
        n = len(peak)
        upper = UPPER_FACTOR * sum(peak) + UPPER_OVERHEAD_S * n
        return Estimate(motion_s=round(sum(peak) + SEGMENT_OVERHEAD_S * n, 2), motion_upper_s=round(upper, 2),
                        recording_upper_s=round(upper + RECORDING_OVERHEAD_S, 2), segments=n)

    def plan_text(self, task: Task, limits: Optional[dict] = None) -> str:
        prog = self.program(task, limits)
        est = self.estimate(task, limits)
        lines = [f"  robot {self.model}, task {task.task}, order {task.order_id}",
                 f"  global speed: {task.params['speed_factor']}%   expected motion ~{est.motion_s:.0f} s "
                 f"(at most ~{est.motion_upper_s:.0f} s)"]
        n = len(prog) - 1
        for i, p in enumerate(prog):
            label = "home" if i in (0, n) else f"#{i}"
            lines.append(f"    MovJ {label:<5} {p}")
        return "\n".join(lines)

    def check_bounds(self, limits: Optional[dict] = None) -> Dict[str, List[int]]:
        env = (limits or PUBLIC_LIMITS)["allowed_envelope_deg"]
        b = {f"q{j + 1}": [math.floor(env[j][0] - 1), math.ceil(env[j][1] + 1)] for j in range(6)}
        for j, v in enumerate(dp.CR3A_SPEC["joint_speed_max_deg_s"]):
            b[f"qd{j + 1}"] = [-math.ceil(v * 1.02), math.ceil(v * 1.02)]
        b["t_ms"] = [0, (MAX_RECORDING_S + 60) * 1000]
        return b

    def verify_preview(self, preview: dict, manifest: dict) -> List[CheckResult]:
        res = []
        bounds = self.check_bounds({"allowed_envelope_deg": manifest["program"]["allowed_envelope_deg"]})
        n = len(preview.get("t_ms", []))
        same_len = all(len(preview.get(c, [])) == n for c in bounds)
        res.append(CheckResult("preview-shape", same_len and n == manifest["preview"]["samples"], f"{n} samples"))
        out = [f"{c}={x}" for c, (lo, hi) in bounds.items() for x in preview.get(c, []) if not lo <= x <= hi]
        res.append(CheckResult("preview-bounds", not out, f"{len(out)} outside" + (f", first {out[:3]}" if out else "")))
        t = preview.get("t_ms", [])
        res.append(CheckResult("preview-time-increasing", all(b > a for a, b in zip(t, t[1:]))))
        return res

    def verify_episode_dir(self, episode_dir: Path, order_raw: Optional[bytes] = None,
                           fingerprint: Optional[str] = None) -> List[CheckResult]:
        return checks.verify_episode_dir(Path(episode_dir), order_raw, fingerprint)

    # ------------------------------------------------------------------ lab
    def probe(self, host: str, dashboard_port: int = 29999, **_) -> RobotStatus:
        """Read-only: dashboard reachable (TCP mode), mode, alarms. Ready = disabled with no alarm (D16)."""
        try:
            db = episode.Dashboard(host, dashboard_port)
        except OSError as e:
            return RobotStatus(reachable=False, ready=False, reason=f"dashboard unreachable: {e}")
        try:
            err = db.cmd("GetErrorID()", ok_codes=None, quiet=True)
            inner = err.split("{", 1)[1].rsplit("}", 1)[0] if "{" in err else ""
            mode = db.robot_mode()
        except (socket.timeout, OSError, ValueError) as e:
            return RobotStatus(reachable=True, ready=False, tcp_mode=False,
                               reason=f"dashboard does not answer (TCP mode off, or DobotStudio Pro connected?): {e}")
        finally:
            db.s.close()
        alarms = inner if any(ch.isdigit() for ch in inner) else ""
        if alarms:
            return RobotStatus(True, False, f"active alarms {alarms}", mode, alarms, True)
        if mode != dp.MODE_DISABLED:
            return RobotStatus(True, False, f"RobotMode {mode} ({dp.ROBOT_MODE.get(mode, '?')}); must be 4 (disabled)",
                               mode, "", True)
        return RobotStatus(True, True, "", mode, "", True)

    def execute(self, task: Task, *, host: str, robot_info: dict, approval, signer, out_dir: Path,
                deal: Deal, preview_budget_bytes: int = 860_000, dashboard_port: int = 29999,
                feedback_port: int = 30004) -> Episode:
        for which, lim in (("public", PUBLIC_LIMITS), ("lab", robot_info)):
            v = self.validate(task, lim)
            if not v.ok:
                raise ValueError(f"task rejected by the {which} limits: " + "; ".join(v.errors))
        est = self.estimate(task, robot_info)
        return episode.record(
            task, program=self.program(task, robot_info), speed=task.params["speed_factor"],
            plan_text=self.plan_text(task, robot_info), host=host, robot_info=robot_info, approval=approval,
            signer=signer, out_dir=out_dir, deal=deal, timeout_s=1.5 * est.motion_upper_s + 30,
            preview_budget_bytes=preview_budget_bytes, dashboard_port=dashboard_port, feedback_port=feedback_port)
