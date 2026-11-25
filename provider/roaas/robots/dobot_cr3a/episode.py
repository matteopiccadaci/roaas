"""Record one CR3A episode: the kit's episode.py (v0.4.5), refactored for ROaaS.

Differences from the kit, all from docs/ARCHITECTURE.md and docs/decisions.md:
  - preflight before anything moves: no alarms, dashboard reachable (TCP mode), robot DISABLED (D16);
  - approval through a policy object instead of input(); the recorder starts only AFTER approval;
  - manifest schema dobot-episode/0.6: no target_host / host_os / python (D7); adds `deal` (D8), `task`,
    `program.targets_deg`, a signed `summary`, `recorder.simulator`, and the preview as signed integer
    series (Crow's series checks read it; signature_valid with jsonEquals ties it to the key);
  - the program comes from the adapter (joint_waypoints or repeatability), the motion timeout from the estimate.
Everything else (framing, CSV columns, statistics, signing) is the kit's.
"""
from __future__ import annotations

import base64
import csv
import datetime as dt
import gzip
import hashlib
import hmac
import io
import os
import re
import statistics
import time
from pathlib import Path
from typing import List, Optional

from ...core.approval import is_local_host
from ...core.canonical import canonical, sha256_file
from ...core.model import Cancelled, Deal, Episode, NotReady, Task
from . import checks
from .controller import Dashboard, Recorder
from .kit import dobot_proto as dp

RECORDER_VERSION = "roaas-0.1.0+kit-0.4.5"
MANIFEST_SCHEMA = "dobot-episode/0.6"
CSV_FIELDS = (["t_controller_ms", "t_pc_ns", "robot_mode", "speed_scaling"]
              + [f"q{j}" for j in range(1, 7)] + [f"qd{j}" for j in range(1, 7)]
              + [f"i{j}" for j in range(1, 7)] + ["x", "y", "z", "rx", "ry", "rz"])


def preflight(db: Dashboard) -> None:
    """Raise NotReady unless the robot may start a job. Sends no motion command."""
    err_reply = db.cmd("GetErrorID()", ok_codes=None)
    inner = err_reply.split("{", 1)[1].rsplit("}", 1)[0] if "{" in err_reply else ""
    if re.search(r"\d", inner):
        raise NotReady(f"controller reports active alarms: {inner}. Clear them in DobotStudio Pro.")
    mode = db.robot_mode()
    if mode == dp.MODE_ERROR:
        raise NotReady("RobotMode = 9 (error). Clear the alarm in DobotStudio Pro.")
    if mode != dp.MODE_DISABLED:
        raise NotReady(f"RobotMode = {mode} ({dp.ROBOT_MODE.get(mode, '?')}): a job starts only from a DISABLED "
                       "robot (mode 4). Disable it and close DobotStudio Pro's connection.")
    reply = db.cmd("RequestControl()", ok_codes=(0, -10000))
    if reply.startswith("-10000"):
        print("    (firmware without RequestControl: TCP mode must be selected in DobotStudio Pro)")


def _move(db: Dashboard, rec: Recorder, program, speed: int, timeout: float, commands: List[str]) -> None:
    """The kit's run_program after the approval step. Always leaves the robot disabled."""
    enabled = False
    try:
        db.cmd("EnableRobot()")
        enabled = True
        time.sleep(1.0)
        db.cmd(f"SpeedFactor({speed})")
        for p in program:
            c = "MovJ(joint={" + ",".join(f"{v:g}" for v in p) + "})"
            db.cmd(c)
            commands.append(c)
        t0, seen_running, last_mode, warned = time.time(), False, None, False
        print("  waiting for the motion to finish (dashboard RobotMode)...")
        while time.time() - t0 < timeout:
            m = db.robot_mode()
            if m != last_mode:
                print(f"    t={time.time() - t0:5.1f} s  RobotMode {m} ({dp.ROBOT_MODE.get(m, '?')})")
                last_mode = m
            seen_running |= m == dp.MODE_RUNNING or rec.last_mode == dp.MODE_RUNNING
            if m == dp.MODE_ERROR:
                raise RuntimeError("the robot went into error during the motion")
            if m == dp.MODE_IDLE and (seen_running or time.time() - t0 > 3):
                if not seen_running:
                    print("    (running state never observed, robot idle after 3 s: assuming done)")
                break
            if rec.error:
                raise RuntimeError(f"recorder stopped: {rec.error}")
            if not warned and time.time() - rec.last_valid_time > 1.0:
                print("    WARNING: no valid feedback packet for >1 s (recorder resyncing)")
                warned = True
            time.sleep(0.2)
        else:
            raise RuntimeError(f"timeout: motion not finished within {timeout:.0f} s")
        time.sleep(0.5)
    finally:
        if enabled:
            for c in ("Stop()", "DisableRobot()"):
                try:
                    db.cmd(c, ok_codes=None)
                except Exception:  # noqa: BLE001
                    pass


def record(task: Task, *, program: List[list], speed: int, plan_text: str, host: str, robot_info: dict,
           approval, signer, out_dir: Path, deal: Deal, timeout_s: float, preview_budget_bytes: int,
           dashboard_port: int = 29999, feedback_port: int = 30004) -> Episode:
    """Preflight, approval, motion and recording, then the signed episode folder. Raises NotReady or
    Cancelled before anything moves; otherwise returns the episode (status completed or aborted)."""
    db = Dashboard(host, dashboard_port)
    try:
        preflight(db)
        if not approval.request(plan_text, host):
            raise Cancelled("cancelled by the operator, no motion command sent")

        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        outdir = Path(out_dir) / f"{task.order_id}_{stamp}"
        outdir.mkdir(parents=True)
        (outdir / "order.json").write_bytes(task.raw)          # exact bytes as received
        print(f"[1] Recorder on {host}:{feedback_port} (starts after approval)")
        rec = Recorder(host, feedback_port, outdir / "raw.bin")
        rec.start()
        time.sleep(0.5)                                       # pre-roll
        if rec.error:
            raise RuntimeError(f"recorder stopped: {rec.error}")
        commands: List[str] = []
        status, abort_reason = "completed", None
        started = time.time()
        print(f"[2] Program to the dashboard {host}:{dashboard_port}")
        try:
            _move(db, rec, program, speed, timeout_s, commands)
        except Exception as e:  # noqa: BLE001
            print(f"[!] Aborted: {e}")
            status, abort_reason = "aborted", str(e)
        time.sleep(0.5)                                       # post-roll
        rec.stop_evt.set()
        rec.join(2)
        duration = time.time() - started
    finally:
        db.s.close()

    print(f"[3] Recorded {len(rec.rows)} packets; resyncs: {len(rec.resyncs)}, dropped frames: {rec.dropped_frames}")
    if not rec.rows:
        raise RuntimeError("no valid packets recorded")
    return _write_episode(task, outdir, rec, program, speed, robot_info, signer, deal, status, abort_reason,
                          duration, commands, stamp, preview_budget_bytes, host)


def _write_episode(task, outdir, rec, program, speed, info, signer, deal, status, abort_reason, duration,
                   commands, stamp, preview_budget_bytes, host) -> Episode:
    from .kit import signers  # needs `cryptography`

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_FIELDS)
    for r in rec.rows:
        w.writerow([r[0], r[1], r[2]] + [f"{v:.6g}" for v in r[3:]])
    with gzip.open(outdir / "telemetry.csv.gz", "wt", compresslevel=9, newline="") as f:
        f.write(buf.getvalue())

    # Values as written to the CSV, so that the signed summary matches what any verifier re-reads.
    _, q, qd, ts = checks.read_telemetry(outdir / "telemetry.csv.gz")
    dts = [b - a for a, b in zip(ts, ts[1:])]
    offsets_ms = [r[1] / 1e6 - r[0] for r in rec.rows]
    spec_v, cont_v = checks.spec_violations(q, qd), checks.continuity_violations(q, ts)
    env = info["allowed_envelope_deg"]
    arr = checks.arrivals(q, program)
    stats = {
        "samples": len(rec.rows), "resyncs": len(rec.resyncs), "dropped_frames": rec.dropped_frames,
        "resync_events": [{"at_sample": i, "skipped_bytes": b} for i, b in rec.resyncs[:50]],
        "message_sizes_seen": {str(k): v for k, v in rec.sizes_seen.items()},
        "duration_s": round(duration, 3),
        "controller_period_ms": {"median": statistics.median(dts) if dts else None,
                                 "max": max(dts) if dts else None,
                                 "gaps_over_16ms": sum(1 for d in dts if d > 16)},
        "pc_minus_controller_clock_ms": {"median": round(statistics.median(offsets_ms), 3),
                                         "spread": round(max(offsets_ms) - min(offsets_ms), 3)},
        "joint_range_seen_deg": [[round(min(r[j] for r in q), 3), round(max(r[j] for r in q), 3)] for j in range(6)],
        "max_abs_joint_speed_deg_s": [round(max(abs(r[j]) for r in qd), 3) for j in range(6)],
        "robot_modes_seen": sorted({r[2] for r in rec.rows}),
        "spec_violations": spec_v, "continuity_violations": cont_v,
    }
    summary_checks = [
        {"id": "run-completed", "ok": status == "completed"},
        {"id": "targets-reached", "ok": all(a["reached"] for a in arr)},
        {"id": "envelope", "ok": checks.envelope_violations(q, env) == 0},
        {"id": "datasheet-limits", "ok": spec_v == 0},
        {"id": "continuity", "ok": cont_v == 0},
        {"id": "timestamps-increasing", "ok": checks.timestamps_increasing(ts)},
    ]
    for a in arr:
        a["t_ms"] = ts[a["sample"]] - ts[0] if a["sample"] is not None else None
    # Signed inside the manifest, so that Crow's signature_valid (jsonEquals) ties the series its checks read.
    preview = checks.choose_preview(q, qd, ts, preview_budget_bytes, keep=[a["sample"] for a in arr])

    first = dp.unpack(rec.first_packet, ["CRRobotType", "Load", "CenterX", "CenterY", "CenterZ",
                                         "UserFrame", "ToolFrame"])
    salt, serial = os.environ.get("DOBOT_SERIAL_SALT", ""), info.get("serial_number", "")
    serial_hmac = hmac.new(salt.encode(), serial.encode(), hashlib.sha256).hexdigest() if salt and serial else None

    pub = signer.public_key()
    (outdir / "pubkey.pem").write_bytes(pub.public_bytes(signers.serialization.Encoding.PEM,
                                                         signers.serialization.PublicFormat.SubjectPublicKeyInfo))
    files = {}
    for name in ["telemetry.csv.gz", "pubkey.pem", "order.json"]:
        p = outdir / name
        files[name] = {"sha256": sha256_file(p), "bytes": p.stat().st_size}

    waypoints = task.params.get("waypoints")
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "status": status,
        "abort_reason": abort_reason,
        "deal": deal.as_manifest(),
        "task": {"robot": task.robot, "task": task.task, "legacy_order": task.legacy,
                 "params": {k: v for k, v in task.params.items() if k != "waypoints"}},
        "order": {"order_id": task.order_id, "nonce": task.nonce,
                  "order_sha256": hashlib.sha256(task.raw).hexdigest(),
                  "waypoints_deg": waypoints, "speed_factor": speed, "issued_at_utc": task.issued_at_utc},
        "program": {"home_deg": info["home_deg"], "targets_deg": program, "commands": commands,
                    "record_only": False, "allowed_envelope_deg": env,
                    "max_speed_factor": int(info.get("max_speed_factor", 20))},
        "robot": {"model": info.get("model"), "serial_hmac_sha256": serial_hmac,
                  "controller_firmware": info.get("controller_firmware"),
                  "calibration_date": info.get("calibration_date"),
                  "robot_type_code": first["CRRobotType"],
                  "payload_setting": {"kg": first["Load"],
                                      "center_mm": [first["CenterX"], first["CenterY"], first["CenterZ"]]},
                  "user_frame": first["UserFrame"], "tool_frame": first["ToolFrame"],
                  "frames_note": "user/tool frame byte split (1200-1295) not yet verified on hardware"},
        "recorder": {"version": RECORDER_VERSION, "simulator": is_local_host(host)},
        "signer": {"type": signer.kind, "description": signer.description,
                   "algorithm": "ECDSA P-256 with SHA-256, DER signature, base64 in manifest.sig",
                   "pubkey_sha256": signers.fingerprint(pub), "key_attestation": None},
        "clock": {"controller": "TimeStamp field of the 30004 packet (Unix ms, controller clock); "
                                "use for intervals, not as absolute time unless the offset below is ~0",
                  "pc": "time.time_ns() at reception; NTP sync not checked by the recorder"},
        "recorded_at_utc": stamp,
        "recorded_at_utc_iso": dt.datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%dT%H:%M:%SZ"),
        "telemetry": {"rate_hz_nominal": 125, "columns": CSV_FIELDS,
                      "units": {"q": "deg", "qd": "deg/s", "i": "controller units (to be confirmed)",
                                "xyz": "mm", "rxyz": "deg"}},
        "files": files,
        "raw_recording": {"name": "raw.bin", "sha256": sha256_file(outdir / "raw.bin"),
                          "bytes": (outdir / "raw.bin").stat().st_size, "travels_with_data": False},
        "stats": stats,
        "summary": {"status": status, "arrivals": arr, "spec_violations": spec_v,
                    "continuity_violations": cont_v, "checks": summary_checks,
                    "all_ok": all(c["ok"] for c in summary_checks)},
        "preview": preview,
    }
    body = canonical(manifest)
    (outdir / "manifest.json").write_bytes(body)
    (outdir / "manifest.sig").write_text(base64.b64encode(signer.sign(body)).decode())
    print(f"[4] Episode saved in {outdir} (signer {signer.kind}, key {signers.fingerprint(pub)[:16]}...), "
          f"status {status}, preview {preview['rate_hz']} Hz")
    return Episode(path=outdir, status=status, abort_reason=abort_reason)
