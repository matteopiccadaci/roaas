"""The lab agent (docs/ARCHITECTURE.md §3.3, Cloudflare version).

It opens ONE outbound WebSocket to the job API's Durable Object and keeps it open:
  cloud -> lab  {"type": "validate", "id", "order_text"}   validate a quote with the lab's own rules (Python)
                {"type": "job", "job": {...}}               a funded job to run
  lab -> cloud  {"type": "hello" | "status", "status": {...}} robot status (probe) every STATUS_EVERY_S
                {"type": "validate_result", "id", "ok", "errors", "warnings", "estimate"}
                {"type": "job_event", "job_id", "state", "detail"}
Results go up over HTTPS (PUT telemetry to R2, then the delivery document). Nothing in a job is executed: it is an
order, parsed and validated again against the LOCAL limits before the operator is asked.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from .. import __version__
from ..core.approval import OnSiteJobConsole
from ..core.model import Cancelled, Deal, Declined, NotReady
from ..core.packaging import build_delivery
from ..core.tasks import TaskError, parse_task
from ..robots import get_adapter

STATUS_EVERY_S = 10
DEADLINE_MARGIN_S = 600          # never start a run this close to the delivery deadline (as Crow's pilot bridge)


class ApprovalWithHook:
    """Wraps an approval policy: tells the cloud the job is running as soon as the operator approves."""

    def __init__(self, policy, on_approved: Callable[[], None]):
        self.policy, self.on_approved, self.name = policy, on_approved, policy.name

    def request(self, program_text: str, host: str) -> bool:
        ok = self.policy.request(program_text, host)
        if ok:
            self.on_approved()
        return ok


class LabAgent:
    def __init__(self, *, cloud: str, token: str, robot_info: dict, host: str, approval, signer, out_dir: Path,
                 robot_id: str = "dobot_cr3a", dashboard_port: int = 29999, feedback_port: int = 30004,
                 on_duty: bool = True, log: Callable[[str], None] = print):
        self.cloud = cloud.rstrip("/")
        self.ws_url = self.cloud.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/api/lab/ws"
        self.token, self.robot_info, self.host = token, robot_info, host
        self.approval, self.signer, self.out_dir = approval, signer, Path(out_dir)
        self.adapter = get_adapter(robot_id)
        self.ports = {"dashboard_port": dashboard_port, "feedback_port": feedback_port}
        self.on_duty, self.log = on_duty, log
        self.jobs: "queue.Queue[dict]" = queue.Queue()
        self.seen: set = set()
        self.busy = False
        self.robot_lock = threading.Lock()      # one dashboard user at a time: probe or job
        self.stop = threading.Event()
        self._ws = None
        self._send_lock = threading.Lock()

    # ------------------------------------------------------------------ messages
    def send(self, msg: dict) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            with self._send_lock:
                ws.send(json.dumps(msg))
        except Exception as e:  # noqa: BLE001 - the receive loop reconnects
            self.log(f"[agent] send failed: {e}")

    def event(self, job_id: str, state: str, detail: str = "") -> None:
        self.log(f"[agent] job {job_id}: {state} {detail}".rstrip())
        self.send({"type": "job_event", "job_id": job_id, "state": state, "detail": detail})

    def status(self) -> dict:
        st = {"robot": self.adapter.robot_id, "agent_version": __version__, "on_duty": self.on_duty, "busy": self.busy,
              "paused": False}
        if self.busy or not self.robot_lock.acquire(blocking=False):
            return dict(st, busy=True, ready=False, reason="running a job")
        try:
            p = self.adapter.probe(self.host, dashboard_port=self.ports["dashboard_port"])
        finally:
            self.robot_lock.release()
        return dict(st, ready=p.ready, reason=p.reason, mode=p.mode)

    def validate(self, order_text: str) -> dict:
        try:
            task, warnings = parse_task(order_text.encode("utf-8"))
        except TaskError as e:
            return {"ok": False, "errors": e.errors, "warnings": []}
        if task.robot != self.adapter.robot_id:
            return {"ok": False, "errors": [f"this lab runs {self.adapter.robot_id}, not {task.robot}"], "warnings": []}
        pub, lab = self.adapter.validate(task), self.adapter.validate(task, self.robot_info)
        errors = pub.errors + [e for e in lab.errors if e not in pub.errors]
        if errors:
            return {"ok": False, "errors": errors, "warnings": warnings + pub.warnings}
        est = self.adapter.estimate(task, self.robot_info)
        return {"ok": True, "errors": [], "warnings": warnings + pub.warnings,
                "estimate": {"motion_s": est.motion_s, "motion_upper_s": est.motion_upper_s,
                             "recording_upper_s": est.recording_upper_s, "segments": est.segments}}

    # ------------------------------------------------------------------ HTTPS uploads
    def put(self, path: str, data: bytes, content_type: str) -> dict:
        req = urllib.request.Request(self.cloud + path, data=data, method="PUT",
                                     headers={"Authorization": f"Bearer {self.token}", "Content-Type": content_type})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"PUT {path}: {e.code} {e.read()[:300]!r}") from None

    # ------------------------------------------------------------------ one job
    def run_job(self, job: dict) -> None:
        jid = job["id"]
        self.event(jid, "claimed")
        raw = job["scope"]["orderText"].encode("utf-8")
        try:
            task, _ = parse_task(raw)
        except TaskError as e:
            return self.event(jid, "declined", "order does not parse: " + "; ".join(e.errors))
        v = self.validate(job["scope"]["orderText"])
        if not v["ok"]:
            return self.event(jid, "declined", "rejected by the lab's limits: " + "; ".join(v["errors"]))
        left_s = job["deliver_by_ms"] / 1000 - time.time()
        if left_s < v["estimate"]["recording_upper_s"] + DEADLINE_MARGIN_S:
            return self.event(jid, "failed", f"too close to the delivery deadline ({left_s:.0f} s left)")
        self.event(jid, "awaiting_approval")
        policy = self.approval
        if policy == "on-site-job":                 # the operator approves THIS job at the robot (ENABLE <code>)
            header = (f"  job {jid}   commitment {job['commitment_id'][:18]}…   buyer {job.get('buyer') or '?'}\n"
                      f"  deliver before {time.strftime('%Y-%m-%d %H:%M', time.localtime(job['deliver_by_ms'] / 1000))}")
            policy = OnSiteJobConsole(jid, header)
        approval = ApprovalWithHook(policy, lambda: self.event(jid, "running"))
        deal = Deal(settlement="crow", commitment_id=job["commitment_id"], buyer=job.get("buyer"), job_id=jid)
        self.robot_lock.acquire()
        self.busy = True
        try:
            ep = self.adapter.execute(task, host=self.host, robot_info=self.robot_info, approval=approval,
                                      signer=self.signer, out_dir=self.out_dir, deal=deal, **self.ports)
        except NotReady as e:
            return self.event(jid, "failed", f"robot not ready: {e}")
        except Declined as e:
            return self.event(jid, "declined", str(e))
        except Cancelled:
            return self.event(jid, "failed", "left for later by the operator: `roaas ops retry " + jid + "` asks again")
        except Exception as e:  # noqa: BLE001
            return self.event(jid, "failed", f"run error: {e}")
        finally:
            self.busy = False
            self.robot_lock.release()
        if ep.status != "completed":
            return self.event(jid, "failed", f"run aborted: {ep.abort_reason}")
        results = self.adapter.verify_episode_dir(ep.path, raw)
        bad = [r.line().strip() for r in results if not r.ok]
        if bad:
            return self.event(jid, "failed", "episode not valid: " + "; ".join(bad[:5]))
        try:
            doc, ciphertext = build_delivery(ep.path, telemetry_url=job["telemetry_url"])
            self.put(f"/api/lab/jobs/{jid}/telemetry", ciphertext, "application/octet-stream")
            self.put(f"/api/lab/jobs/{jid}/episode", doc, "application/json")
        except Exception as e:  # noqa: BLE001
            return self.event(jid, "failed", f"upload failed: {e}")
        self.log(f"[agent] job {jid}: delivered to the cloud ({len(doc) // 1024} KB), episode {ep.path}")

    def _worker(self) -> None:
        while not self.stop.is_set():
            try:
                job = self.jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.run_job(job)
            except Exception as e:  # noqa: BLE001
                self.log(f"[agent] job {job.get('id')} crashed: {e}")
            self.send({"type": "status", "status": self.status()})

    def _status_loop(self) -> None:
        while not self.stop.wait(STATUS_EVERY_S):
            self.send({"type": "status", "status": self.status()})

    # ------------------------------------------------------------------ main loop
    def run(self, connect=None) -> None:
        """Connect, reconnect with backoff, and serve until stop is set (Ctrl+C)."""
        if connect is None:
            from websockets.sync.client import connect
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._status_loop, daemon=True).start()
        backoff = 1
        while not self.stop.is_set():
            try:
                with connect(self.ws_url, additional_headers={"Authorization": f"Bearer {self.token}"},
                             open_timeout=10, max_size=2 ** 20) as ws:
                    self._ws, backoff = ws, 1
                    self.log(f"[agent] connected to {self.ws_url}")
                    self.send({"type": "hello", "status": self.status()})
                    while not self.stop.is_set():
                        try:
                            raw = ws.recv(timeout=1)
                        except TimeoutError:
                            continue
                        self._handle(json.loads(raw))
            except Exception as e:  # noqa: BLE001
                if self.stop.is_set():
                    break
                self.log(f"[agent] connection lost ({e}); retrying in {backoff} s")
            finally:
                self._ws = None
            self.stop.wait(backoff)
            backoff = min(backoff * 2, 60)

    def _handle(self, m: dict) -> None:
        if m.get("type") == "validate":
            r = self.validate(str(m.get("order_text", "")))
            self.send(dict(r, type="validate_result", id=m.get("id")))
        elif m.get("type") == "job":
            job = m["job"]
            if job["id"] in self.seen and not m.get("retry"):
                return
            self.seen.add(job["id"])
            self.log(f"[agent] new job {job['id']} for commitment {job['commitment_id'][:18]}…")
            self.jobs.put(job)
