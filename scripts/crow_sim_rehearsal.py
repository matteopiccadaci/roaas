"""Full local rehearsal of one ROaaS deal on Crow's simulator.

pnpm sim (local chain + 2 operators + adjudicators)  <-  Crow seller adapter --sim (our seller.json, rehearsal terms)
      ^                                                        |  quote/start/result
      |                                                        v
buy-with-signer.ts (Crow's buyer)                       ROaaS Worker (wrangler dev)  <-WS-  lab agent  ->  fake CR3A

Nothing touches a real robot (fake_dobot.py) or a real network (Crow's local chain, test tokens only).

Usage, from the roaas repository:
  python scripts/crow_sim_rehearsal.py <built clone of github.com/rafaeldavid/crow> . <empty work dir> <python with roaas deps>
Needs: Node >= 22.18 with corepack (pnpm 12.16.1), `npm install` in cloud/, Python with cryptography + websockets.
Logs of every process stay in the work dir (sim.log, worker.log, fake.log, agent.log, adapter.log, buyer-*.out).
"""
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

CROW, REPO, WORK, PY = map(Path, sys.argv[1:5])
WORK.mkdir(parents=True, exist_ok=True)
KIT = REPO / "kits" / "dobot_kit"
ADAPTER_KEY, LAB_TOKEN = "sim-adapter-key-123", "sim-lab-token-456"
OPS = "http://127.0.0.1:8787,http://127.0.0.1:8788"
PRICING = {"token": "tUSD", "deliver_within_sec": 86400,
           "joint_waypoints": {"formula": "base + per_waypoint x waypoints", "base": "20000000", "per_waypoint": "1000000"},
           "repeatability": {"formula": "x", "base": "30000000", "per_waypoint": "1000000"}}
procs = []


def start(name, args, cwd, env=None):
    log = open(WORK / f"{name}.log", "w")
    p = subprocess.Popen(args, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, env={**os.environ, **(env or {})},
                         start_new_session=True)
    procs.append((name, p))
    return p


def wait_port(port, timeout=120, name=""):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return
        except OSError:
            time.sleep(0.5)
    raise SystemExit(f"{name}: port {port} never opened; see {WORK}/{name}.log")


def wait_log(name, pattern, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        text = (WORK / f"{name}.log").read_text(errors="replace")
        if re.search(pattern, text):
            return text
        time.sleep(0.5)
    raise SystemExit(f"{name}: '{pattern}' not seen; see {WORK}/{name}.log")


def stop_all():
    for name, p in reversed(procs):
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


try:
    pnpm = ["corepack", "pnpm@12.16.1"]
    start("sim", pnpm + ["sim"], CROW, {"COREPACK_ENABLE_DOWNLOAD_PROMPT": "0"})
    wait_port(8780, name="sim"); wait_port(8787, name="sim"); wait_port(8788, name="sim")
    print("[e2e] Crow sim up")

    start("worker", ["npx", "--no-install", "wrangler", "dev", "--port", "8799", "--ip", "127.0.0.1",
                     "--persist-to", str(WORK / "wstate"), "--var", f"ADAPTER_KEY:{ADAPTER_KEY}",
                     "--var", f"LAB_TOKEN:{LAB_TOKEN}", "--var", f"OPERATORS:{OPS}",
                     "--var", "PUBLIC_BASE:https://roaas-dev.example.workers.dev",
                     "--var", f"PRICING_JSON:{json.dumps(PRICING)}"], REPO / "cloud",
          {"WRANGLER_SEND_METRICS": "false", "CI": "1"})
    wait_port(8799, name="worker")
    print("[e2e] ROaaS Worker up (wrangler dev :8799)")

    start("fake", [str(PY), "fake_dobot.py", "--dashboard-port", "39997", "--feedback-port", "39002"], KIT)
    wait_port(39997, name="fake")

    # the lab key, then the rehearsal terms pinned to it
    env_py = {"PYTHONPATH": str(REPO / "provider")}
    fp = subprocess.run([str(PY), "-c", "from roaas.robots.dobot_cr3a.kit import signers as s; import sys;"
                         "print(s.fingerprint(s.make_signer('software', sys.argv[1]).public_key()))", str(WORK / "keys")],
                        capture_output=True, text=True, env={**os.environ, **env_py}).stdout.strip().splitlines()[-1]
    (WORK / "pricing.json").write_text(json.dumps(PRICING))
    subprocess.run([str(PY), "-m", "roaas.cli", "offers", "build", "--fingerprint", fp, "--job-api", "http://127.0.0.1:8799",
                    "--out", str(WORK / "seller"), "--rehearsal", "--pricing", str(WORK / "pricing.json")],
                   check=True, env={**os.environ, **env_py})
    print(f"[e2e] rehearsal terms pinned to lab key {fp[:16]}…")

    start("agent", [str(PY), "-m", "roaas.cli", "agent", "--cloud", "http://127.0.0.1:8799", "--robot-info",
                    str(KIT / "lab_limits_cr3a.json"), "--dashboard-port", "39997", "--feedback-port", "39002",
                    "--keys-dir", str(WORK / "keys"), "--out", str(WORK / "episodes")], WORK,
          {**env_py, "ROAAS_LAB_TOKEN": LAB_TOKEN, "PYTHONUNBUFFERED": "1"})
    wait_log("agent", r"connected to")
    print("[e2e] lab agent connected")

    start("adapter", ["node", str(CROW / "examples" / "seller-adapter.ts"), str(WORK / "seller" / "seller.json"), "--sim"],
          WORK, {"ROAAS_JOB_API_KEY": ADAPTER_KEY})
    wait_log("adapter", r"serves POST", 180)
    print("[e2e] Crow seller adapter up (:8791)")

    order = subprocess.run([str(PY), "-c", "from roaas.core.tasks import encode_task; import sys; sys.stdout.write("
                            "encode_task('dobot_cr3a','joint_waypoints','sim-deal-001','5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a',"
                            "{'speed_factor':100,'waypoints':[[5,-5,10,10,-10,20],[-5,5,-10,-10,10,-20]]},"
                            "issued_at_utc='2026-11-25T00:00:00Z').decode())"],
                           capture_output=True, text=True, env={**os.environ, **env_py}).stdout
    (WORK / "order.json").write_text(order)
    scope = json.loads(subprocess.run([str(PY), "-m", "roaas.cli", "scope", str(WORK / "order.json")],
                                      capture_output=True, text=True, env={**os.environ, **env_py}).stdout)
    buyer_env = {"HOLDBACK_OPERATORS": OPS}
    r = subprocess.run(["node", str(CROW / "examples" / "buy-with-signer.ts"), "http://127.0.0.1:8791/quote",
                        str(WORK / "buyer-keys"), json.dumps(scope), "100000000", "http://127.0.0.1:8791/defaults"],
                       cwd=WORK, capture_output=True, text=True, env={**os.environ, **buyer_env}, timeout=300)
    (WORK / "buyer-quote.out").write_text(r.stdout + "\n--- stderr ---\n" + r.stderr)
    print(f"[e2e] buyer quote: exit {r.returncode}")
    approve = [l for l in r.stderr.splitlines() if "approve" in l and "buy-with-signer" in l]
    if r.returncode != 3 or not approve:
        raise SystemExit(f"buyer did not stop for approval; see {WORK}/buyer-quote.out")
    cid = re.search(r"0x[0-9a-f]{64}", approve[-1]).group(0)
    print(f"[e2e] offer {cid[:18]}… shown to the buyer; approving (funds the hold)")
    r = subprocess.run(["node", str(CROW / "examples" / "buy-with-signer.ts"), "approve", str(WORK / "buyer-keys"), cid,
                        "100000000"], cwd=WORK, capture_output=True, text=True, env={**os.environ, **buyer_env}, timeout=900)
    (WORK / "buyer-approve.out").write_text(r.stdout + "\n--- stderr ---\n" + r.stderr)
    print(f"[e2e] buyer approve: exit {r.returncode}")
    tail = (r.stderr.strip().splitlines() or [""])[-12:]
    print("\n".join("    " + l for l in tail))
finally:
    stop_all()
