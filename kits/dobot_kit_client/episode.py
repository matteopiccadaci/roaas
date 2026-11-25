#!/usr/bin/env python3
"""Run an order (home -> waypoints -> home) on a Dobot CR3A and record the episode from port 30004.

Output folder:
  telemetry.csv.gz  data that travels with the dataset (joints, speeds, currents, tool pose)
  raw.bin           every 1440-byte packet: STAYS WITH THE LAB, only its hash is in the manifest
  manifest.json     metadata, nonce, waypoints, statistics, file hashes
  manifest.sig      ECDSA P-256 signature of manifest.json (base64, DER)
  pubkey.pem        public key to verify the signature

Safety:
  - moving a robot that is not on localhost requires --live;
  - home and waypoints must lie inside allowed_envelope_deg in robot_info.json;
  - the speed factor is capped by max_speed_factor in robot_info.json;
  - the run aborts if the controller reports alarms;
  - with --live the program is printed and the operator must type ENABLE;
  - at the end, or on any error, Stop() and DisableRobot() are sent.
"""
import argparse
import base64
import csv
import datetime as dt
import gzip
import hashlib
import hmac
import io
import json
import os
import platform
import re
import socket
import struct
import statistics
import sys
import threading
import time
from pathlib import Path

import dobot_proto as dp
import orders
import signers

KIT_VERSION = "0.4.4"
CSV_FIELDS = (["t_controller_ms", "t_pc_ns", "robot_mode", "speed_scaling"]
              + [f"q{j}" for j in range(1, 7)] + [f"qd{j}" for j in range(1, 7)]
              + [f"i{j}" for j in range(1, 7)] + ["x", "y", "z", "rx", "ry", "rz"])


# ---------------------------------------------------------------- helpers
def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


# ---------------------------------------------------------------- dashboard (port 29999)
class Dashboard:
    def __init__(self, host, port):
        self.s = socket.create_connection((host, port), timeout=5)

    def cmd(self, text, ok_codes=(0,), quiet=False):
        """Send one command and wait for 'ErrorID,{...},Cmd();'. ok_codes=None accepts any code."""
        self.s.sendall(text.encode())
        buf = b""
        while not buf.rstrip().endswith(b";"):
            chunk = self.s.recv(4096)
            if not chunk:
                raise ConnectionError("dashboard connection closed")
            buf += chunk
        reply = buf.decode().strip()
        code = int(reply.split(",", 1)[0])
        if not quiet:
            print(f"  > {text:<55} < {reply}")
        if ok_codes is not None and code not in ok_codes:
            raise RuntimeError(f"command rejected: {text} -> {reply}")
        return reply

    def robot_mode(self):
        reply = self.cmd("RobotMode()", ok_codes=None, quiet=True)
        try:
            return int(reply.split("{", 1)[1].split("}", 1)[0])
        except (IndexError, ValueError):
            return None


# ---------------------------------------------------------------- recorder (port 30004)
class Recorder(threading.Thread):
    """Reads port 30004. Packets are framed on the fixed TestValue at byte 48: if the stream
    ever gets misaligned (e.g. the controller sends a packet of a different length), the
    recorder searches for the next TestValue and carries on, logging what it skipped."""
    NAMES = ["RobotMode", "TimeStamp", "TestValue", "SpeedScaling", "QActual", "QDActual",
             "IActual", "ToolVectorActual", "MessageSize"]
    MAGIC = struct.pack("<Q", dp.TEST_VALUE)

    def __init__(self, host, port, raw_path):
        super().__init__(daemon=True)
        self.sock = socket.create_connection((host, port), timeout=5)
        self.raw = open(raw_path, "wb")
        self.rows = []
        self.resyncs = []          # list of (packet index, skipped bytes)
        self.sizes_seen = {}       # MessageSize values seen
        self.dropped_frames = 0    # frames rejected because the next frame was not aligned
        self.first_packet = None
        self.stop_evt = threading.Event()
        self.last_mode = None
        self.last_valid_time = time.time()
        self.error = None

    def run(self):
        buf = bytearray()
        P, M = dp.PACKET_SIZE, self.MAGIC
        try:
            while not self.stop_evt.is_set():
                chunk = self.sock.recv(65536)
                if not chunk:
                    raise ConnectionError("feedback stream closed by the controller")
                t = time.time_ns()
                buf += chunk
                while len(buf) >= P:
                    if buf[48:56] != M:
                        idx = buf.find(M, 49)
                        if idx == -1:          # keep the tail, a magic may straddle chunks
                            skip = len(buf) - 55
                            del buf[:skip]
                            self.resyncs.append((len(self.rows), skip))
                            break
                        skip = idx - 48
                        del buf[:skip]
                        self.resyncs.append((len(self.rows), skip))
                        continue
                    # look-ahead: accept a frame only if the NEXT frame starts exactly 1440 bytes later.
                    # A truncated packet followed by a full one would otherwise be glued into one bogus frame.
                    if len(buf) < P + 56:
                        break
                    if buf[P + 48:P + 56] != M:
                        idx = buf.find(M, 49)
                        skip = (idx - 48) if idx != -1 else len(buf) - 55
                        del buf[:skip]
                        self.resyncs.append((len(self.rows), skip))
                        self.dropped_frames += 1
                        continue
                    pkt = bytes(buf[:P]); del buf[:P]
                    d = dp.unpack(pkt, self.NAMES)
                    self.sizes_seen[d["MessageSize"]] = self.sizes_seen.get(d["MessageSize"], 0) + 1
                    if self.first_packet is None:
                        self.first_packet = pkt
                    self.raw.write(pkt)
                    self.last_mode = d["RobotMode"]
                    self.last_valid_time = time.time()
                    self.rows.append([d["TimeStamp"], t, d["RobotMode"], d["SpeedScaling"],
                                      *d["QActual"], *d["QDActual"], *d["IActual"], *d["ToolVectorActual"]])
        except Exception as e:  # noqa: BLE001
            self.error = e
        finally:
            self.raw.close()
            self.sock.close()


# ---------------------------------------------------------------- motion
def run_program(db, rec, program, speed, live, timeout, commands):
    """Send the program and wait for completion. Always leaves the robot disabled.
    Completion is read from the dashboard (RobotMode() on 29999), with the feedback stream as backup."""
    enabled = False
    try:
        err_reply = db.cmd("GetErrorID()", ok_codes=None)
        inner = err_reply.split("{", 1)[1].rsplit("}", 1)[0] if "{" in err_reply else ""
        if re.search(r"\d", inner):
            raise RuntimeError(f"controller reports active alarms: {inner}. Clear them in DobotStudio Pro.")
        if db.robot_mode() == dp.MODE_ERROR or rec.last_mode == dp.MODE_ERROR:
            raise RuntimeError("RobotMode = 9 (error). Clear the alarm in DobotStudio Pro.")
        reply = db.cmd("RequestControl()", ok_codes=(0, -10000))
        if reply.startswith("-10000"):
            print("    (firmware without RequestControl: TCP mode must be selected in DobotStudio Pro)")
        if live:
            print("\n  PROGRAM TO RUN ON THE REAL ROBOT")
            print(f"  global speed: {speed}%")
            for p in program:
                print(f"    MovJ joints {p}")
            ans = input("\n  Emergency stop within reach and work area clear? Type ENABLE to proceed: ")
            if ans.strip().upper() != "ENABLE":
                raise RuntimeError("cancelled by the operator, no motion command sent")
        db.cmd("EnableRobot()")
        enabled = True
        time.sleep(1.0)
        db.cmd(f"SpeedFactor({speed})")
        for p in program:
            c = "MovJ(joint={" + ",".join(f"{v:g}" for v in p) + "})"
            db.cmd(c)
            commands.append(c)
        t0 = time.time()
        seen_running = False
        last_mode = None
        warned = False
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
            raise RuntimeError(f"timeout: motion not finished within {timeout} s")
        time.sleep(0.5)
    finally:
        if enabled:
            for c in ("Stop()", "DisableRobot()"):
                try:
                    db.cmd(c, ok_codes=None)
                except Exception:  # noqa: BLE001
                    pass


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--order", required=True, help="JSON with order_id, nonce, waypoints, speed_factor")
    ap.add_argument("--robot-info", required=True, help="JSON with robot metadata and allowed envelope")
    ap.add_argument("--out", default="episodes")
    ap.add_argument("--keys-dir", default="keys")
    ap.add_argument("--signer", choices=["software", "tpm"], default="software",
                    help="tpm = key held in the Windows TPM (run tpm_setup.ps1 once first)")
    ap.add_argument("--feedback-port", type=int, default=30004)
    ap.add_argument("--dashboard-port", type=int, default=29999)
    ap.add_argument("--timeout", type=float, default=90, help="max seconds for the motion")
    ap.add_argument("--live", action="store_true", help="required to move a real robot")
    ap.add_argument("--record-only", type=float, metavar="SEC",
                    help="send no commands at all: just record for SEC seconds")
    args = ap.parse_args()

    if args.host not in ("127.0.0.1", "localhost") and not args.live and args.record_only is None:
        sys.exit("Non-local host: add --live to move a real robot (and stay next to the emergency stop).")

    order_bytes = Path(args.order).read_bytes()
    order = json.loads(order_bytes)
    info = json.loads(Path(args.robot_info).read_text())
    home = info["home_deg"]
    waypoints = order["waypoints"]
    speed = int(order.get("speed_factor", 15))
    program = [home] + waypoints + [home]
    if args.record_only is None:
        errs, warns = orders.validate_order(order, info)
        for w in warns:
            print(f"[order] warning: {w}")
        if errs:
            sys.exit("Order rejected:\n  " + "\n  ".join(errs))

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outdir = Path(args.out) / f"{order['order_id']}_{stamp}"
    outdir.mkdir(parents=True)
    signer = signers.make_signer(args.signer, args.keys_dir)
    (outdir / "order.json").write_bytes(order_bytes)  # exact bytes as received

    print(f"[1] Connecting the recorder to {args.host}:{args.feedback_port}")
    rec = Recorder(args.host, args.feedback_port, outdir / "raw.bin")
    rec.start()
    time.sleep(0.5)  # pre-roll
    if rec.error:
        sys.exit(f"Recorder stopped: {rec.error}")

    commands = []
    status, abort_reason = "completed", None
    started = time.time()
    if args.record_only is None:
        print(f"[2] Sending the program to the dashboard {args.host}:{args.dashboard_port}")
        try:
            db = Dashboard(args.host, args.dashboard_port)
            run_program(db, rec, program, speed, args.live, args.timeout, commands)
        except Exception as e:  # noqa: BLE001
            print(f"[!] Aborted: {e}")
            status, abort_reason = "aborted", str(e)
    else:
        print(f"[2] Record only, {args.record_only} s, no commands sent")
        time.sleep(args.record_only)
    time.sleep(0.5)  # post-roll
    rec.stop_evt.set()
    rec.join(2)
    duration = time.time() - started
    skipped = sum(b for _, b in rec.resyncs)
    print(f"[3] Recorded {len(rec.rows)} packets; resyncs: {len(rec.resyncs)} ({skipped} bytes skipped, "
          f"{rec.dropped_frames} damaged frames dropped)")
    if not rec.rows:
        sys.exit("No valid packets recorded.")

    # ---------- telemetry for the buyer
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_FIELDS)
    for r in rec.rows:
        w.writerow([r[0], r[1], r[2]] + [f"{v:.6g}" for v in r[3:]])
    with gzip.open(outdir / "telemetry.csv.gz", "wt", compresslevel=9, newline="") as f:
        f.write(buf.getvalue())

    # ---------- statistics and checks
    t_ctrl = [r[0] for r in rec.rows]
    dts = [b - a for a, b in zip(t_ctrl, t_ctrl[1:])]
    offsets_ms = [r[1] / 1e6 - r[0] for r in rec.rows]
    spec = dp.CR3A_SPEC
    violations = []
    for k, r in enumerate(rec.rows):
        for j in range(6):
            q, qd = r[4 + j], r[10 + j]
            lo, hi = spec["joint_range_deg"][j]
            if not lo <= q <= hi:
                violations.append(f"sample {k}: J{j + 1} position {q:.3f}")
            if abs(qd) > spec["joint_speed_max_deg_s"][j] * 1.02:
                violations.append(f"sample {k}: J{j + 1} speed {qd:.3f}")
    continuity = 0
    for a, b in zip(rec.rows, rec.rows[1:]):
        dt_s = max((b[0] - a[0]) / 1000, 0.001)
        for j in range(6):
            if abs(b[4 + j] - a[4 + j]) > spec["joint_speed_max_deg_s"][j] * dt_s * 1.2 + 0.05:
                continuity += 1
    stats = {
        "samples": len(rec.rows),
        "resyncs": len(rec.resyncs),
        "dropped_frames": rec.dropped_frames,
        "resync_events": [{"at_sample": i, "skipped_bytes": b} for i, b in rec.resyncs[:50]],
        "message_sizes_seen": {str(k): v for k, v in rec.sizes_seen.items()},
        "duration_s": round(duration, 3),
        "controller_period_ms": {"median": statistics.median(dts) if dts else None,
                                 "max": max(dts) if dts else None,
                                 "gaps_over_16ms": sum(1 for d in dts if d > 16)},
        "pc_minus_controller_clock_ms": {"median": round(statistics.median(offsets_ms), 3),
                                         "spread": round(max(offsets_ms) - min(offsets_ms), 3)},
        "joint_range_seen_deg": [[round(min(r[4 + j] for r in rec.rows), 3),
                                  round(max(r[4 + j] for r in rec.rows), 3)] for j in range(6)],
        "max_abs_joint_speed_deg_s": [round(max(abs(r[10 + j]) for r in rec.rows), 3) for j in range(6)],
        "robot_modes_seen": sorted({r[2] for r in rec.rows}),
        "spec_violations": len(violations),
        "continuity_violations": continuity,
    }

    first = dp.unpack(rec.first_packet, ["CRRobotType", "Load", "CenterX", "CenterY", "CenterZ",
                                         "UserFrame", "ToolFrame"])
    salt = os.environ.get("DOBOT_SERIAL_SALT", "")
    serial = info.get("serial_number", "")
    serial_hmac = hmac.new(salt.encode(), serial.encode(), hashlib.sha256).hexdigest() if salt and serial else None

    pub = signer.public_key()
    (outdir / "pubkey.pem").write_bytes(pub.public_bytes(signers.serialization.Encoding.PEM,
                                                         signers.serialization.PublicFormat.SubjectPublicKeyInfo))
    names = ["telemetry.csv.gz", "pubkey.pem", "order.json"]
    if signer.cert_der:
        (outdir / "signer_cert.cer").write_bytes(signer.cert_der)
        names.append("signer_cert.cer")
    files = {}
    for name in names:
        p = outdir / name
        files[name] = {"sha256": sha256_file(p), "bytes": p.stat().st_size}

    manifest = {
        "schema": "dobot-episode/0.5",
        "status": status,
        "abort_reason": abort_reason,
        "order": {"order_id": order["order_id"], "nonce": order["nonce"],
                  "order_sha256": hashlib.sha256(order_bytes).hexdigest(),
                  "waypoints_deg": waypoints, "speed_factor": speed,
                  "issued_at_utc": order.get("issued_at_utc")},
        "program": {"home_deg": home, "commands": commands, "record_only": args.record_only is not None,
                    "allowed_envelope_deg": info["allowed_envelope_deg"],
                    "max_speed_factor": int(info.get("max_speed_factor", 20))},
        "robot": {"model": info.get("model"),
                  "serial_hmac_sha256": serial_hmac,
                  "controller_firmware": info.get("controller_firmware"),
                  "calibration_date": info.get("calibration_date"),
                  "robot_type_code": first["CRRobotType"],
                  "payload_setting": {"kg": first["Load"],
                                      "center_mm": [first["CenterX"], first["CenterY"], first["CenterZ"]]},
                  "user_frame": first["UserFrame"],
                  "tool_frame": first["ToolFrame"],
                  "frames_note": "user/tool frame byte split (1200-1295) not yet verified on hardware"},
        "recorder": {"kit_version": KIT_VERSION, "host_os": platform.platform(),
                     "python": platform.python_version(), "target_host": args.host},
        "signer": {"type": signer.kind, "description": signer.description,
                   "algorithm": "ECDSA P-256 with SHA-256, DER signature, base64 in manifest.sig",
                   "pubkey_sha256": signers.fingerprint(pub),
                   "key_attestation": None},
        "clock": {"controller": "TimeStamp field of the 30004 packet (Unix ms, controller clock); "
                                "use for intervals, not as absolute time unless the offset below is ~0",
                  "pc": "time.time_ns() at reception; NTP sync not checked by the kit"},
        "recorded_at_utc": stamp,
        "recorded_at_utc_iso": dt.datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%dT%H:%M:%SZ"),
        "telemetry": {"rate_hz_nominal": 125, "columns": CSV_FIELDS,
                      "units": {"q": "deg", "qd": "deg/s", "i": "controller units (to be confirmed)",
                                "xyz": "mm", "rxyz": "deg"}},
        "files": files,
        "raw_recording": {"name": "raw.bin", "sha256": sha256_file(outdir / "raw.bin"),
                          "bytes": (outdir / "raw.bin").stat().st_size, "travels_with_data": False},
        "stats": stats,
    }

    body = canonical(manifest)
    (outdir / "manifest.json").write_bytes(body)
    sig = signer.sign(body)
    (outdir / "manifest.sig").write_text(base64.b64encode(sig).decode())
    if violations:
        (outdir / "violations.txt").write_text("\n".join(violations))

    print(f"[4] Episode saved in {outdir}  (signer: {signer.kind}, key {signers.fingerprint(pub)[:16]}...)")
    print(f"    telemetry.csv.gz: {files['telemetry.csv.gz']['bytes'] / 1024:.0f} KB   "
          f"raw.bin: {manifest['raw_recording']['bytes'] / 1024:.0f} KB")
    print(f"    median controller period {stats['controller_period_ms']['median']} ms, "
          f"gaps >16 ms: {stats['controller_period_ms']['gaps_over_16ms']}, "
          f"spec violations: {len(violations)}, continuity violations: {continuity}")
    if status != "completed":
        print(f"    STATUS: {status.upper()} ({abort_reason}) - episode saved for diagnosis")
        sys.exit(1)


if __name__ == "__main__":
    main()
