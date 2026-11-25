"""Task parsing (new and legacy formats), validation against limits (reject, never clamp), estimates."""
import json

import pytest

from conftest import KIT
from roaas.core.tasks import TaskError, encode_task, parse_task
from roaas.robots import get_adapter

CR3A = get_adapter("dobot_cr3a")
NONCE = "3f9a6c1e8b2d47f0a5c9e1b7d3f20864"


def new_task(task="joint_waypoints", **params):
    p = {"speed_factor": 30, "waypoints": [[5, -5, 10, 10, -10, 20]]}
    p.update(params)
    return parse_task(encode_task("dobot_cr3a", task, "t-001", NONCE, p))[0]


def test_legacy_order_file_is_accepted_as_is():
    raw = (KIT / "example_order.json").read_bytes()
    task, warnings = parse_task(raw)
    assert task.legacy and task.robot == "dobot_cr3a" and task.task == "joint_waypoints"
    assert task.raw == raw and task.params["speed_factor"] == 30 and len(task.params["waypoints"]) == 3
    assert CR3A.validate(task).ok


def test_new_format_roundtrip_is_compact_ascii():
    raw = encode_task("dobot_cr3a", "joint_waypoints", "t-1", NONCE, {"speed_factor": 30, "waypoints": [[1, 2, 3, 4, 5, 6]]})
    assert b"\n" not in raw and raw.isascii()
    task, _ = parse_task(raw)
    assert not task.legacy and task.raw == raw


@pytest.mark.parametrize("obj, needle", [
    ({"schema": "roaas-task/1", "robot": "dobot_cr3a", "task": "joint_waypoints", "order_id": "x", "nonce": "zz", "params": {}}, "nonce"),
    ({"schema": "roaas-task/9", "robot": "dobot_cr3a", "task": "t", "order_id": "x", "nonce": NONCE, "params": {}}, "schema"),
    ({"schema": "roaas-task/1", "robot": "Dobot!", "task": "t", "order_id": "x", "nonce": NONCE, "params": {}}, "robot"),
    ({"order_id": "x", "nonce": NONCE, "waypoints": []}, "speed_factor"),
])
def test_bad_orders_raise(obj, needle):
    with pytest.raises(TaskError) as e:
        parse_task(json.dumps(obj).encode())
    assert any(needle in m for m in e.value.errors)


def test_out_of_envelope_is_rejected_not_clamped():
    t = new_task(waypoints=[[25, 0, 0, 0, 0, 0]])          # J1 envelope is [-20, 20]
    v = CR3A.validate(t)
    assert not v.ok and any("J1 = 25" in e for e in v.errors)
    assert t.params["waypoints"] == [[25, 0, 0, 0, 0, 0]]     # untouched


def test_speed_and_shape_rules():
    assert not CR3A.validate(new_task(speed_factor=101)).ok
    assert not CR3A.validate(new_task(speed_factor=0)).ok
    assert not CR3A.validate(new_task(waypoints=[[1, 2, 3]])).ok
    assert not CR3A.validate(new_task(waypoints=[[1, 2, 3, 4, 5, 6]] * 11)).ok


def test_lab_limits_are_applied_too():
    t = new_task(waypoints=[[15, 0, 0, 0, 0, 0]])
    tighter = dict(CR3A.public_limits(), allowed_envelope_deg=[[-10, 10]] + [[-30, 30]] * 5)
    assert CR3A.validate(t).ok and not CR3A.validate(t, tighter).ok


def test_repeatability_rules_and_program():
    ok = new_task("repeatability", repetitions=3)
    assert CR3A.validate(ok).ok
    home = CR3A.public_limits()["home_deg"]
    wp = ok.params["waypoints"][0]
    assert CR3A.program(ok) == [home, wp, home, wp, home, wp, home]
    assert not CR3A.validate(new_task("repeatability", repetitions=1)).ok
    assert not CR3A.validate(new_task("repeatability", repetitions=3, waypoints=[[1, 1, 1, 1, 1, 1]] * 6)).ok
    assert not CR3A.validate(new_task("repetitions_typo")).ok


def test_estimate_calibrated_on_the_real_arm():
    task, _ = parse_task((KIT / "example_order.json").read_bytes())
    est = CR3A.estimate(task)
    assert 8.0 <= est.motion_s <= 10.0                 # real CR3A: 8.9 s of motion (2026-11-25)
    assert est.motion_upper_s >= 14.5                  # the fake controller's minimum-jerk model: 14.5 s
    assert est.segments == 4


def test_too_long_recording_is_refused_at_validation():
    slow = new_task(speed_factor=1, waypoints=[[20, 30, 45, 90, 90, 180], [-20, -30, -45, -90, -90, -180]] * 3)
    v = CR3A.validate(slow)
    assert not v.ok and any("exceeds the maximum" in e for e in v.errors)


def test_check_bounds_are_integers_with_margin():
    b = CR3A.check_bounds()
    assert b["q1"] == [-21, 21] and b["q6"] == [-181, 181]
    assert all(isinstance(x, int) for lo_hi in b.values() for x in lo_hi)


def test_preview_rate_drops_to_fit_long_recordings():
    import math
    from roaas.core.canonical import canonical
    from roaas.robots.dobot_cr3a.checks import choose_preview
    n = 125 * 600                                            # 10 minutes at 125 Hz
    q = [[round(20 * math.sin(k / 400 + j), 3) for j in range(6)] for k in range(n)]
    qd = [[round(10 * math.cos(k / 400 + j), 3) for j in range(6)] for k in range(n)]
    ts = [k * 8 for k in range(n)]
    p = choose_preview(q, qd, ts, 860_000)
    assert len(canonical(p)) <= 860_000 and p["rate_hz"] < 125 and p["t_ms"][-1] == ts[-1] - ts[0]
