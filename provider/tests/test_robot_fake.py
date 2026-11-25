"""End to end against the kit's fake CR3A controller (real time: a few seconds per run)."""
import json
import subprocess
import sys
import zipfile

import pytest

from conftest import KIT
from roaas.core import approval
from roaas.core.canonical import sha256_hex
from roaas.core.model import Cancelled, Deal, NotReady
from roaas.core.packaging import build_delivery, reconstruct_episode_dir
from roaas.core.tasks import encode_task, parse_task
from roaas.core.verify import verify_delivery
from roaas.robots import get_adapter
from roaas.robots.dobot_cr3a.controller import Dashboard
from roaas.settlement.manual import package_zip

pytestmark = pytest.mark.robot
CR3A = get_adapter("dobot_cr3a")
NONCE = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
DEAL = Deal(settlement="crow", commitment_id="0x" + "ab" * 32, buyer="0x" + "cd" * 20, job_id="job-1")


def make_task(task="joint_waypoints", **params):
    p = {"speed_factor": 100, "waypoints": [[5, -5, 10, 10, -10, 20], [-5, 5, -10, -10, 10, -20]]}
    p.update(params)
    return parse_task(encode_task("dobot_cr3a", task, f"{task}-test", NONCE, p,
                                  issued_at_utc="2026-11-25T00:00:00Z"))[0]


def run(task, robot, robot_info, signer, out, policy=None, deal=DEAL):
    return CR3A.execute(task, host="127.0.0.1", robot_info=robot_info, approval=policy or approval.SimulatorOnly(),
                        signer=signer, out_dir=out, deal=deal, **robot.ports)


@pytest.fixture(scope="module")
def recorded(fake_robot, robot_info, signer, tmp_path_factory):
    task = make_task()
    ep = run(task, fake_robot, robot_info, signer, tmp_path_factory.mktemp("episodes"))
    return task, ep


def test_episode_is_valid_and_v06(recorded):
    task, ep = recorded
    assert ep.status == "completed"
    results = CR3A.verify_episode_dir(ep.path, task.raw)
    assert all(r.ok for r in results), [r.line() for r in results if not r.ok]
    m = json.loads((ep.path / "manifest.json").read_text())
    assert m["schema"] == "dobot-episode/0.6"
    assert set(m["recorder"]) == {"version"}                        # no target_host, host_os, python (D7)
    assert m["deal"]["commitment_id"] == DEAL.commitment_id         # bound into the signature (D8)
    assert m["summary"]["all_ok"] and len(m["summary"]["arrivals"]) == 4
    assert max(a["max_error_deg"] for a in m["summary"]["arrivals"]) < 0.1


def test_recorder_starts_after_approval(recorded):
    _, ep = recorded
    import csv, gzip
    with gzip.open(ep.path / "telemetry.csv.gz", "rt") as f:
        rows = list(csv.DictReader(f))
    t = [int(r["t_controller_ms"]) for r in rows]
    first_motion = next(k for k, r in enumerate(rows) if r["robot_mode"] == "7")
    assert (t[first_motion] - t[0]) / 1000 < 2.5                    # pre-roll + enable only, no operator wait


def test_kit_verifier_still_accepts_v06(recorded):
    # The kit's verify_episode.py reads top-level "waypoints" from --order, so for a roaas-task/1 order it can
    # check the nonce only; legacy orders keep the full --order check (test below).
    task, ep = recorded
    r = subprocess.run([sys.executable, str(KIT / "verify_episode.py"), str(ep.path), "--nonce", task.nonce],
                       cwd=KIT, capture_output=True, text=True)
    assert r.returncode == 0 and "RESULT: episode VALID" in r.stdout, r.stdout


def test_legacy_order_end_to_end_with_kit_verifier(fake_robot, robot_info, signer, tmp_path):
    legacy = json.dumps({"order_id": "legacy-001", "nonce": NONCE, "issued_at_utc": "2026-11-25T00:00:00Z",
                         "speed_factor": 100, "waypoints": [[6, -6, 12, 12, -12, 24]]}, indent=2).encode()
    task, _ = parse_task(legacy)
    ep = run(task, fake_robot, robot_info, signer, tmp_path / "ep", deal=Deal(settlement="manual"))
    (tmp_path / "my_order.json").write_bytes(legacy)
    r = subprocess.run([sys.executable, str(KIT / "verify_episode.py"), str(ep.path), "--order",
                        str(tmp_path / "my_order.json")], cwd=KIT, capture_output=True, text=True)
    assert r.returncode == 0 and "RESULT: episode VALID" in r.stdout, r.stdout


def test_crow_delivery_roundtrip(recorded, tmp_path):
    task, ep = recorded
    doc_bytes, ct = build_delivery(ep.path, telemetry_url="https://example.invalid/t.enc")
    assert len(doc_bytes) < 1_000_000 and b"\\n" not in doc_bytes        # no newline anywhere (hidden-chars check)
    fp = json.loads(doc_bytes)["manifest"]["signer"]["pubkey_sha256"]
    light = verify_delivery(doc_bytes, expected_order=task.raw, fingerprint=fp)
    assert all(r.ok for r in light), [r.line() for r in light if not r.ok]
    full = verify_delivery(doc_bytes, expected_order=task.raw, fingerprint=fp, telemetry_ciphertext=ct)
    assert all(r.ok for r in full), [r.line() for r in full if not r.ok]
    assert any(r.id == "full:targets-reached" for r in full)

    # the buyer can also rebuild the folder and run the kit's own verifier
    from roaas.core import crypto
    doc = json.loads(doc_bytes)
    plain = crypto.decrypt(ct, crypto.unb64(doc["full_telemetry"]["key"]))
    folder = reconstruct_episode_dir(doc, plain, tmp_path / "rebuilt")
    r = subprocess.run([sys.executable, str(KIT / "verify_episode.py"), str(folder), "--nonce", task.nonce,
                        "--fingerprint", fp], cwd=KIT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    assert sha256_hex((folder / "order.json").read_bytes()) == doc["manifest"]["order"]["order_sha256"]


def _tampered(doc_bytes, mutate):
    doc = json.loads(doc_bytes)
    mutate(doc)
    return json.dumps(doc, separators=(",", ":")).encode()


@pytest.mark.parametrize("name, mutate, failing", [
    ("preview value", lambda d: d["preview"]["q1"].__setitem__(3, 0.5), "preview-hash"),
    ("shown manifest", lambda d: d["manifest"]["summary"].__setitem__("all_ok", True) or
        d["manifest"]["stats"].__setitem__("samples", 1), "manifest-equals-signed-text"),
    ("signed text", lambda d: d.__setitem__("manifest_text", d["manifest_text"].replace('"completed"', '"complete!"', 1)), "signature"),
    ("order", lambda d: d.__setitem__("order_text", d["order_text"].replace("100", "99", 1)), "order-hash"),
    ("preview out of envelope", lambda d: d["preview"]["q1"].__setitem__(0, 40.0), "preview-bounds"),
])
def test_tampering_is_detected(recorded, name, mutate, failing):
    _, ep = recorded
    doc_bytes, _ = build_delivery(ep.path, telemetry_url="https://example.invalid/t.enc")
    results = {r.id: r.ok for r in verify_delivery(_tampered(doc_bytes, mutate))}
    assert results.get(failing) is False, (name, results)


def test_wrong_fingerprint_and_corrupt_telemetry(recorded):
    task, ep = recorded
    doc_bytes, ct = build_delivery(ep.path, telemetry_url="https://example.invalid/t.enc")
    res = {r.id: r.ok for r in verify_delivery(doc_bytes, fingerprint="00" * 32)}
    assert res["fingerprint"] is False
    bad = bytearray(ct)
    bad[40] ^= 1
    res = {r.id: r.ok for r in verify_delivery(doc_bytes, telemetry_ciphertext=bytes(bad))}
    assert res["telemetry-ciphertext-hash"] is False and res["telemetry-decrypt"] is False


def test_manual_zip_keeps_kit_layout(recorded, tmp_path):
    _, ep = recorded
    z = package_zip(ep.path, tmp_path)
    names = {n.split("/", 1)[1] for n in zipfile.ZipFile(z).namelist()}
    assert names == {"manifest.json", "manifest.sig", "telemetry.csv.gz", "pubkey.pem", "order.json", "preview.json"}


def test_enabled_robot_is_not_ready(fresh_fake_robot, robot_info, signer, tmp_path):
    db = Dashboard("127.0.0.1", fresh_fake_robot.dashboard_port)
    db.cmd("EnableRobot()")                                # as if DobotStudio Pro had left it enabled
    db.s.close()
    status = CR3A.probe("127.0.0.1", dashboard_port=fresh_fake_robot.dashboard_port)
    assert status.reachable and not status.ready and status.mode == 5
    with pytest.raises(NotReady):
        run(make_task(), fresh_fake_robot, robot_info, signer, tmp_path)
    assert not any(tmp_path.iterdir())                     # nothing recorded


def test_declined_job_moves_nothing(fresh_fake_robot, robot_info, signer, tmp_path):
    assert CR3A.probe("127.0.0.1", dashboard_port=fresh_fake_robot.dashboard_port).ready
    with pytest.raises(Cancelled):
        run(make_task(), fresh_fake_robot, robot_info, signer, tmp_path, policy=approval.Never())
    assert not any(tmp_path.iterdir())


def test_simulator_policy_refuses_a_real_robot():
    with pytest.raises(PermissionError):
        approval.SimulatorOnly().request("program", "192.168.5.1")


def test_out_of_limits_rejected_before_connecting(robot_info, signer, tmp_path):
    t = make_task(waypoints=[[25, 0, 0, 0, 0, 0]])
    with pytest.raises(ValueError):                        # no fake robot running: never reaches the network
        CR3A.execute(t, host="127.0.0.1", robot_info=robot_info, approval=approval.SimulatorOnly(), signer=signer,
                     out_dir=tmp_path, deal=DEAL, dashboard_port=1, feedback_port=1)


def test_repeatability_run(fake_robot, robot_info, signer, tmp_path):
    t = make_task("repeatability", waypoints=[[8, -8, 12, 15, -15, 30]], repetitions=2)
    ep = run(t, fake_robot, robot_info, signer, tmp_path)
    results = CR3A.verify_episode_dir(ep.path, t.raw)
    assert all(r.ok for r in results), [r.line() for r in results if not r.ok]
    m = json.loads((ep.path / "manifest.json").read_text())
    assert len(m["summary"]["arrivals"]) == 5 and m["task"]["params"]["repetitions"] == 2


def test_cli_validate_legacy_example():
    r = subprocess.run([sys.executable, "-m", "roaas.cli", "validate", str(KIT / "example_order.json")],
                       cwd=KIT.parents[1] / "provider", capture_output=True, text=True)
    assert r.returncode == 0 and "Order buyer-demo-0001 OK" in r.stdout, r.stdout + r.stderr


def test_cli_manual_run_and_verify_delivery(fresh_fake_robot, tmp_path):
    """The operator's command line: run (manual path + Crow-style delivery), then the buyer's verify-delivery."""
    provider = KIT.parents[1] / "provider"
    env = dict(__import__("os").environ, PYTHONPATH=str(provider))
    order = KIT / "example_order.json"
    legacy_fast = json.loads(order.read_text())
    legacy_fast["speed_factor"] = 100
    (tmp_path / "order.json").write_text(json.dumps(legacy_fast))
    r = subprocess.run([sys.executable, "-m", "roaas.cli", "run", str(tmp_path / "order.json"), "--robot-info",
                        str(KIT / "lab_limits_cr3a.json"), "--crow-delivery",
                        "--dashboard-port", str(fresh_fake_robot.dashboard_port),
                        "--feedback-port", str(fresh_fake_robot.feedback_port)],
                       cwd=tmp_path, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    doc = next((tmp_path / "deliveries").glob("*.roaas-episode.json"))
    enc = doc.with_name(doc.name.replace(".roaas-episode.json", ".telemetry.enc"))
    fp = json.loads(doc.read_text())["manifest"]["signer"]["pubkey_sha256"]
    r = subprocess.run([sys.executable, "-m", "roaas.cli", "verify-delivery", str(doc), "--order",
                        str(tmp_path / "order.json"), "--fingerprint", fp, "--telemetry", str(enc)],
                       cwd=tmp_path, env=env, capture_output=True, text=True)
    assert r.returncode == 0 and "RESULT: delivery VALID" in r.stdout, r.stdout + r.stderr
