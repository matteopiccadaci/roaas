"""Phase 5, end to end on this machine: the real Worker under `wrangler dev` (local Durable Object and R2), the lab
agent over its WebSocket, the fake CR3A, and this test playing Crow's seller adapter (quote, start, result).

Run with ROAAS_CLOUD_E2E=1 (needs `npm install` in cloud/). With CROW_REPO set, the delivery also goes through
Crow's own check-delivery.ts against the rehearsal terms.
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

from conftest import KIT, REPO, free_port
from roaas.core import approval, crypto
from roaas.core.tasks import encode_task
from roaas.core.verify import verify_delivery

pytestmark = [pytest.mark.robot,
              pytest.mark.skipif(os.environ.get("ROAAS_CLOUD_E2E") != "1", reason="set ROAAS_CLOUD_E2E=1")]
ADAPTER_KEY, LAB_TOKEN = "test-adapter-key", "test-lab-token"
PRICING = {"token": "0x00000000000000000000000000000000000000aa", "deliver_within_sec": 86400,
           "joint_waypoints": {"formula": "base + per_waypoint x waypoints", "base": "20000000", "per_waypoint": "1000000"},
           "repeatability": {"formula": "x", "base": "30000000", "per_waypoint": "1000000"}}


def call(base, method, path, body=None, key=ADAPTER_KEY, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json", **({"X-ROaaS-Key": key} if key else {}), **(headers or {})}
    req = urllib.request.Request(base + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@pytest.fixture(scope="module")
def cloud(tmp_path_factory):
    port = free_port()
    log = open(tmp_path_factory.mktemp("wrangler-log") / "wrangler.log", "w")
    env = dict(os.environ, WRANGLER_SEND_METRICS="false", CI="1")
    proc = subprocess.Popen(
        ["npx", "--no-install", "wrangler", "dev", "--port", str(port), "--ip", "127.0.0.1",
         "--persist-to", str(tmp_path_factory.mktemp("wrangler-state")),
         "--var", f"ADAPTER_KEY:{ADAPTER_KEY}", "--var", f"LAB_TOKEN:{LAB_TOKEN}", "--var", "SKIP_HELD_CHECK:1",
         # the public https address the deployed Worker would have (links in deliveries must be https)
         "--var", "PUBLIC_BASE:https://roaas-dev.example.workers.dev",
         "--var", f"PRICING_JSON:{json.dumps(PRICING)}"],
        cwd=REPO / "cloud", stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
    base = f"http://127.0.0.1:{port}"
    t0 = time.time()
    while time.time() - t0 < 90:
        try:
            if call(base, "GET", "/api/status", key=None)[0] == 200:
                break
        except OSError:
            pass
        time.sleep(0.5)
    else:
        import signal
        os.killpg(proc.pid, signal.SIGTERM)
        pytest.fail("wrangler dev did not start; see " + log.name)
    yield base
    import signal
    os.killpg(proc.pid, signal.SIGTERM)                    # wrangler AND its workerd child (which holds ports)
    proc.wait(10)


@pytest.fixture(scope="module")
def agent(cloud, fake_robot, robot_info, signer, tmp_path_factory):
    from roaas.agent.agent import LabAgent
    lines = []
    a = LabAgent(cloud=cloud, token=LAB_TOKEN, robot_info=robot_info, host="127.0.0.1",
                 approval=approval.SimulatorOnly(), signer=signer, out_dir=tmp_path_factory.mktemp("agent-episodes"),
                 log=lines.append, **fake_robot.ports)
    th = threading.Thread(target=a.run, daemon=True)
    th.start()
    t0 = time.time()
    while time.time() - t0 < 30 and not json.loads(call(cloud, "GET", "/api/status", key=None)[1]).get("accepting"):
        time.sleep(0.5)
    yield a, lines
    a.stop.set()


def scope_of(order: bytes, task="joint_waypoints"):
    from roaas.settlement.crow_terms import scope_for_order
    s = scope_for_order(order)
    assert s["task"] == task
    return s


ORDER = encode_task("dobot_cr3a", "joint_waypoints", "cloud-e2e-001", "0123456789abcdef0123456789abcdef",
                    {"speed_factor": 100, "waypoints": [[5, -5, 10, 10, -10, 20], [-5, 5, -10, -10, 10, -20]]},
                    issued_at_utc="2026-11-25T00:00:00Z")


def test_status_shows_the_lab_online(cloud, agent):
    st = json.loads(call(cloud, "GET", "/api/status", key=None)[1])
    assert st["online"] and st["accepting"] and st["ready"] and st["robot"] == "dobot_cr3a", st


def test_quote_guards(cloud, agent):
    scope = scope_of(ORDER)
    assert call(cloud, "POST", "/api/crow/quote", {"scope": scope}, key="wrong")[0] == 401
    bad = dict(scope, orderSha256="0" * 64)
    code, body = call(cloud, "POST", "/api/crow/quote", {"buyer": "0xb", "scope": bad})
    assert code == 400 and b"orderSha256" in body
    far = encode_task("dobot_cr3a", "joint_waypoints", "far-001", "fedcba9876543210fedcba9876543210",
                      {"speed_factor": 30, "waypoints": [[25, 0, 0, 0, 0, 0]]})
    code, body = call(cloud, "POST", "/api/crow/quote", {"buyer": "0xb", "scope": scope_of(far)})
    assert code == 422 and b"J1 = 25" in body                       # refused by the LAB's own Python rules


def test_validate_is_public_and_detailed(cloud, agent):
    code, body = call(cloud, "POST", "/api/validate", {"orderText": ORDER.decode()}, key=None)
    assert code == 200 and json.loads(body)["estimate"]["segments"] == 3


def test_full_job_through_the_cloud(cloud, agent, tmp_path):
    a, lines = agent
    scope = scope_of(ORDER)
    code, body = call(cloud, "POST", "/api/crow/quote", {"buyer": "0x" + "b" * 40, "scope": scope})
    assert code == 200, body
    q = json.loads(body)
    assert q["price"] == {"token": PRICING["token"], "amount": "22000000"} and q["deliverWithinSec"] > 7200
    cid = "0x" + "c1" * 32
    start = {"commitmentId": cid, "quoteId": q["quoteId"], "scope": scope}
    code, body = call(cloud, "POST", "/api/crow/jobs", start, headers={"Idempotency-Key": cid})
    assert code == 200, body
    job_id = json.loads(body)["jobId"]
    assert json.loads(call(cloud, "POST", "/api/crow/jobs", start, headers={"Idempotency-Key": cid})[1])["jobId"] == job_id
    t0 = time.time()
    while time.time() - t0 < 90:
        code, body = call(cloud, "GET", f"/api/crow/jobs/{job_id}/result")
        if code == 200:
            break
        assert code == 202, body
        time.sleep(1)
    else:
        pytest.fail("no result; agent log:\n" + "\n".join(lines))
    doc = json.loads(body)
    assert doc["manifest"]["deal"]["commitment_id"] == cid and doc["order_text"] == ORDER.decode()
    assert doc["full_telemetry"]["url"] == f"https://roaas-dev.example.workers.dev/t/{job_id}.enc"
    code, ciphertext = call(cloud, "GET", f"/t/{job_id}.enc", key=None)
    assert code == 200
    results = verify_delivery(body, expected_order=ORDER, fingerprint=doc["manifest"]["signer"]["pubkey_sha256"],
                              telemetry_ciphertext=ciphertext)
    assert all(r.ok for r in results), [r.line() for r in results if not r.ok]
    assert json.loads(call(cloud, "GET", "/api/status", key=None)[1])["queue"] == 0

    if os.environ.get("CROW_REPO"):
        from roaas.robots import get_adapter
        from roaas.settlement import crow_terms as ct
        terms = ct.terms_for(ct.template(get_adapter("dobot_cr3a"), doc["manifest"]["signer"]["pubkey_sha256"],
                                         rehearsal=True), scope)
        (tmp_path / "terms.json").write_text(json.dumps(terms))
        (tmp_path / "delivery.json").write_bytes(body)
        r = subprocess.run(["node", "examples/check-delivery.ts", str(tmp_path / "terms.json"), str(tmp_path / "delivery.json")],
                           cwd=os.environ["CROW_REPO"], capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr


def lab_call(base, method, path, body=None):
    return call(base, method, path, body, key=None, headers={"Authorization": f"Bearer {LAB_TOKEN}"})


def test_operator_pause_and_off_duty(cloud, agent):
    scope = scope_of(ORDER)
    assert lab_call(cloud, "POST", "/api/lab/ops/pause", {})[0] == 200
    code, body = call(cloud, "POST", "/api/crow/quote", {"scope": scope})
    assert code == 503 and b"paused by the operator" in body
    st = json.loads(lab_call(cloud, "POST", "/api/lab/ops/resume", {})[1])
    assert st["accepting"] and not st["paused"]
    lab_call(cloud, "POST", "/api/lab/ops/close", {})
    code, body = call(cloud, "POST", "/api/crow/quote", {"scope": scope})
    assert code == 503 and b"operator off duty" in body
    assert json.loads(lab_call(cloud, "POST", "/api/lab/ops/open", {})[1])["accepting"]
    assert call(cloud, "POST", "/api/lab/ops/pause", {}, key=None)[0] == 401          # needs the lab token
    r = subprocess.run([sys.executable, "-m", "roaas.cli", "ops", "status", "--cloud", cloud], capture_output=True,
                       text=True, timeout=60, env=dict(os.environ, ROAAS_LAB_TOKEN=LAB_TOKEN, PYTHONPATH=str(REPO / "provider")))
    assert r.returncode == 0 and "ACCEPTING JOBS" in r.stdout, r.stdout + r.stderr


def test_lab_offline_means_no_quotes_and_jobs_wait(cloud, agent):
    a, _ = agent
    order2 = encode_task("dobot_cr3a", "joint_waypoints", "cloud-e2e-002", "abcdefabcdefabcdefabcdefabcdef01",
                         {"speed_factor": 100, "waypoints": [[3, -3, 6, 6, -6, 12]]})
    scope2 = scope_of(order2)
    code, body = call(cloud, "POST", "/api/crow/quote", {"scope": scope2})
    assert code == 200, body
    quote2 = json.loads(body)["quoteId"]
    a.stop.set()
    t0 = time.time()
    while time.time() - t0 < 15 and json.loads(call(cloud, "GET", "/api/status", key=None)[1])["online"]:
        time.sleep(0.5)
    code, body = call(cloud, "POST", "/api/crow/quote", {"buyer": "0xb", "scope": scope_of(ORDER)})
    assert code == 503 and b"not available" in body
    # a deal funded while the lab is away waits; the operator can decline it, or ask for it again
    cid = "0x" + "d2" * 32
    code, body = call(cloud, "POST", "/api/crow/jobs", {"commitmentId": cid, "quoteId": quote2, "scope": scope2})
    assert code == 200, body
    job = json.loads(body)["jobId"]
    assert lab_call(cloud, "POST", f"/api/lab/jobs/{job}/decline", {"reason": "robot under maintenance"})[0] == 200
    assert {j["id"]: j["state"] for j in json.loads(lab_call(cloud, "GET", "/api/lab/jobs")[1])}[job] == "declined"
    code, body = lab_call(cloud, "POST", f"/api/lab/jobs/{job}/retry", {})
    assert code == 200 and json.loads(body)["pushed"] is False                       # nobody connected
    assert {j["id"]: j["state"] for j in json.loads(lab_call(cloud, "GET", "/api/lab/jobs")[1])}[job] == "started"
    assert call(cloud, "GET", f"/api/crow/jobs/{job}/result")[0] == 202
