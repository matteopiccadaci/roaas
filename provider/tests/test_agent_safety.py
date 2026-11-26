"""The lab agent's safety guards, one job at a time, against the kit's fake CR3A (no cloud: the agent's messages and
uploads are captured). They mirror what Crow's one-run pilot bridge does on the robot PC."""
import base64
import json
import os
import subprocess
import sys
import time

import pytest

from conftest import KIT, REPO
from roaas.agent.agent import LabAgent
from roaas.core import approval
from roaas.core.tasks import encode_task
from roaas.robots import get_adapter
from roaas.settlement import crow_terms as ct

pytestmark = pytest.mark.robot
CR3A = get_adapter("dobot_cr3a")
ORDER = encode_task("dobot_cr3a", "joint_waypoints", "agent-safety-001", "0f1e2d3c4b5a69788796a5b4c3d2e1f0",
                    {"speed_factor": 100, "waypoints": [[4, -4, 8, 8, -8, 16]]}, issued_at_utc="2026-11-26T00:00:00Z")


class Recorder:
    """An approval policy that remembers when it was asked and what existed on disk at that moment."""

    def __init__(self, out_dir, answer=True, then=None):
        self.out_dir, self.answer, self.then = out_dir, answer, then
        self.name, self.asked_at, self.dirs_at_approval = "test", None, None

    def request(self, program_text, host):
        self.asked_at = time.time()
        self.dirs_at_approval = sorted(p.name for p in self.out_dir.iterdir())
        if self.then:
            self.then()
        time.sleep(1.0)                          # the operator takes a moment: nothing may record meanwhile
        self.approved_at = time.time()
        return self.answer


def make_agent(fake_robot, robot_info, signer, out, *, rehearsal=True, fp=None, policy=None, terms=None):
    fp = fp or CR3A_fp(signer)
    sent, uploads = [], []

    def fetch(job):
        if isinstance(terms, Exception):
            raise terms
        return terms if terms is not None else ct.terms_for(ct.template(CR3A, fp, rehearsal=rehearsal), job["scope"])

    a = LabAgent(cloud="http://127.0.0.1:9", token="t", robot_info=robot_info, host="127.0.0.1",
                 approval=policy or approval.SimulatorOnly(), signer=signer, out_dir=out, log=lambda s: None,
                 fetch_terms=fetch, **fake_robot.ports)
    a.send = sent.append
    a.put = lambda path, data, ctype: uploads.append((path, len(data))) or {"ok": True}
    return a, sent, uploads


def CR3A_fp(signer):
    from roaas.robots.dobot_cr3a.kit import signers
    return signers.fingerprint(signer.public_key())


def job(deliver_in_s=86400, order=ORDER):
    return {"id": "job-0123456789abcdef01234567", "commitment_id": "0x" + "ee" * 32, "buyer": "0x" + "bb" * 20,
            "scope": ct.scope_for_order(order), "deliver_by_ms": int((time.time() + deliver_in_s) * 1000),
            "telemetry_url": "https://roaas-dev.example.workers.dev/t/job-0123456789abcdef01234567.enc"}


def states(sent):
    return [(m["state"], m.get("detail", "")) for m in sent if m.get("type") == "job_event"]


def test_rehearsal_deal_is_checked_then_uploaded(fake_robot, robot_info, signer, tmp_path):
    pol = Recorder(tmp_path)
    a, sent, uploads = make_agent(fake_robot, robot_info, signer, tmp_path, policy=pol)
    a.run_job(job())
    assert [s for s, _ in states(sent)] == ["claimed", "awaiting_approval", "running"], states(sent)
    assert [p for p, _ in uploads] == ["/api/lab/jobs/job-0123456789abcdef01234567/telemetry",
                                       "/api/lab/jobs/job-0123456789abcdef01234567/episode"]
    # the recorder starts only after approval: no episode folder while the operator was asked, and the first
    # telemetry sample is later than the answer
    assert pol.dirs_at_approval == []
    import csv, gzip
    ep = next(tmp_path.iterdir())
    with gzip.open(ep / "telemetry.csv.gz", "rt") as f:
        first_pc_s = int(next(csv.DictReader(f))["t_pc_ns"]) / 1e9
    assert first_pc_s >= pol.approved_at
    assert a.current is None


def test_production_terms_against_the_simulator_are_declined_before_asking(fake_robot, robot_info, signer, tmp_path):
    pol = Recorder(tmp_path)
    a, sent, uploads = make_agent(fake_robot, robot_info, signer, tmp_path, rehearsal=False, policy=pol)
    a.run_job(job())
    assert states(sent)[-1][0] == "declined" and "production terms" in states(sent)[-1][1]
    assert pol.asked_at is None and uploads == [] and list(tmp_path.iterdir()) == []


def test_terms_that_do_not_name_the_order_are_declined(fake_robot, robot_info, signer, tmp_path):
    other = encode_task("dobot_cr3a", "joint_waypoints", "other-001", "ffffffffffffffffffffffffffffffff",
                        {"speed_factor": 100, "waypoints": [[1, 1, 1, 1, 1, 1]]})
    wrong = ct.terms_for(ct.template(CR3A, CR3A_fp(signer), rehearsal=True), ct.scope_for_order(other))
    a, sent, _ = make_agent(fake_robot, robot_info, signer, tmp_path, terms=wrong)
    a.run_job(job())
    assert states(sent)[-1] == ("declined", "the deal's terms do not name this order (orderSha256)")


def test_unreadable_terms_fail_the_job_and_ask_nobody(fake_robot, robot_info, signer, tmp_path):
    pol = Recorder(tmp_path)
    a, sent, _ = make_agent(fake_robot, robot_info, signer, tmp_path, policy=pol, terms=RuntimeError("operators down"))
    a.run_job(job())
    assert states(sent)[-1][0] == "failed" and "operators down" in states(sent)[-1][1] and pol.asked_at is None


def test_deadline_is_checked_again_after_approval(fake_robot, robot_info, signer, tmp_path):
    j = job()
    pol = Recorder(tmp_path, then=lambda: j.update(deliver_by_ms=int((time.time() + 300) * 1000)))   # hours pass
    a, sent, uploads = make_agent(fake_robot, robot_info, signer, tmp_path, policy=pol)
    a.run_job(j)
    last = states(sent)[-1]
    assert last[0] == "failed" and "too late to run" in last[1] and "nothing moved" in last[1], last
    assert "running" not in [s for s, _ in states(sent)]
    assert uploads == [] and list(tmp_path.iterdir()) == []              # the recorder never started


def test_a_delivery_the_deal_would_refuse_is_not_uploaded(fake_robot, robot_info, signer, tmp_path):
    # terms pinned to ANOTHER key's fingerprint (e.g. the lab key was replaced): the signature check would refund
    a, sent, uploads = make_agent(fake_robot, robot_info, signer, tmp_path, fp="1" * 64)
    a.run_job(job())
    last = states(sent)[-1]
    assert last[0] == "failed" and "would refuse this delivery" in last[1] and "manifest-signature" in last[1], last
    assert uploads == []


def test_a_crash_inside_a_job_is_reported_as_failed(fake_robot, robot_info, signer, tmp_path):
    a, sent, _ = make_agent(fake_robot, robot_info, signer, tmp_path)
    a.run_job = lambda j: (_ for _ in ()).throw(KeyError("scope"))
    a.jobs.put(job())
    import threading
    th = threading.Thread(target=a._worker, daemon=True)
    th.start()
    t0 = time.time()
    while time.time() - t0 < 10 and not states(sent):
        time.sleep(0.1)
    a.stop.set()
    assert states(sent) and states(sent)[0][0] == "failed" and "lab agent error" in states(sent)[0][1]


def test_status_names_the_job_in_hand(fake_robot, robot_info, signer, tmp_path):
    seen = []
    pol = Recorder(tmp_path, answer=False, then=lambda: seen.append(a.status()["job_id"]))
    a, sent, _ = make_agent(fake_robot, robot_info, signer, tmp_path, policy=pol)
    a.run_job(job())
    assert seen == ["job-0123456789abcdef01234567"] and a.status()["job_id"] is None


# ------------------------------------------------------------------------------------------------ the console
def test_typeahead_is_dropped_before_the_prompt():
    calls = []
    c = approval.OnSiteJobConsole("job-abcd", ask=lambda p: calls.append("ask") or "ENABLE ABCD",
                                  show=lambda s: calls.append("show"), flush=lambda: calls.append("flush"))
    assert c.request("program", "192.168.5.1") is True
    assert calls == ["show", "flush", "ask"]
    calls.clear()
    c = approval.OnSiteConsole(ask=lambda p: calls.append("ask") or "", show=lambda s: calls.append("show"),
                               flush=lambda: calls.append("flush"))
    assert c.request("program", "192.168.5.1") is False and calls == ["show", "flush", "ask"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX terminal; on Windows the msvcrt path is tried by hand on the mini PC")
def test_flush_really_discards_keys_typed_in_advance():
    import pty
    master, slave = pty.openpty()
    with os.fdopen(slave, "r", closefd=True) as tty:
        os.write(master, b"ENABLE ABCD\n")                     # typed before the prompt appeared
        time.sleep(0.2)
        approval.flush_typeahead(tty)
        os.write(master, b"DECLINE\n")                         # typed after
        assert tty.readline().strip() == "DECLINE"
    os.close(master)


# ------------------------------------------------------------------------------------------------ the CLI
@pytest.mark.parametrize("cmd", [
    ["run", str(KIT / "example_order.json"), "--robot-info", str(KIT / "lab_limits_cr3a.json"), "--live"],
    ["agent", "--cloud", "https://example.invalid", "--robot-info", str(KIT / "lab_limits_cr3a.json"), "--live"],
])
def test_live_is_refused_on_the_simulator(cmd, tmp_path):
    r = subprocess.run([sys.executable, "-m", "roaas.cli", *cmd], cwd=tmp_path, capture_output=True, text=True,
                       timeout=60, env=dict(os.environ, ROAAS_LAB_TOKEN="x", PYTHONPATH=str(REPO / "provider")))
    assert r.returncode != 0 and "--live is for the real robot" in r.stderr, r.stdout + r.stderr


def test_every_http_request_names_roaas():
    """Cloudflare answers 403 (error 1010) to Python's default user agent, on our Worker and on Crow's API."""
    import roaas
    for f in ("roaas/agent/agent.py", "roaas/cli.py"):
        src = (REPO / "provider" / f).read_text()
        assert src.count("urllib.request.Request(") == src.count('"User-Agent": USER_AGENT'), f
    assert roaas.USER_AGENT.startswith("roaas/") and "Python-urllib" not in roaas.USER_AGENT
