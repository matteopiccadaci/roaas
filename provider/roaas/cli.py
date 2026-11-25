"""ROaaS command line (phase 3: lab-side commands; queue and agent commands come in phases 5-6).

  roaas validate ORDER [--limits FILE]          parse, validate, estimate, print the program
  roaas probe --host IP                         read-only robot status (ready = disabled, no alarm)
  roaas run ORDER --robot-info FILE [--host IP --live] [--crow-delivery]
                                                manual path: approve, run, record, sign, verify, zip
  roaas verify-delivery DOC [--order FILE --fingerprint HEX --telemetry FILE]

Moving a robot that is not on this machine needs --live, and then the operator types ENABLE.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from .core import approval as approvals
from .core.model import Cancelled, Deal, NotReady
from .core.packaging import build_delivery
from .core.tasks import TaskError, parse_task
from .core.verify import verify_delivery
from .robots import get_adapter


def _load_task(path):
    try:
        task, warnings = parse_task(Path(path).read_bytes())
    except TaskError as e:
        sys.exit("Order REJECTED:\n  " + "\n  ".join(e.errors))
    for w in warnings:
        print(f"  WARNING  {w}")
    return task


def cmd_validate(a):
    task = _load_task(a.order)
    adapter = get_adapter(task.robot)
    limits = json.loads(Path(a.limits).read_text()) if a.limits else None
    v = adapter.validate(task, limits)
    for w in v.warnings:
        print(f"  WARNING  {w}")
    for e in v.errors:
        print(f"  ERROR    {e}")
    if not v.ok:
        sys.exit("\nThe order will be REJECTED.")
    print(f"Order {task.order_id} OK ({task.robot} / {task.task})")
    print(adapter.plan_text(task, limits))
    est = adapter.estimate(task, limits)
    print(f"  recording at most ~{est.recording_upper_s:.0f} s")


def cmd_probe(a):
    st = get_adapter(a.robot).probe(a.host, dashboard_port=a.dashboard_port)
    print(json.dumps(st.__dict__, indent=2))
    sys.exit(0 if st.ready else 1)


def cmd_run(a):
    from .robots.dobot_cr3a.kit import signers
    from .settlement.manual import package_zip

    task = _load_task(a.order)
    adapter = get_adapter(task.robot)
    info = json.loads(Path(a.robot_info).read_text(encoding="utf-8"))
    local = approvals.is_local_host(a.host)
    if not local and not a.live:
        sys.exit("Non-local host: add --live to move a real robot (and stay next to the emergency stop).")
    policy = approvals.OnSiteConsole() if a.live else approvals.SimulatorOnly()
    signer = signers.make_signer(a.signer, a.keys_dir)
    try:
        ep = adapter.execute(task, host=a.host, robot_info=info, approval=policy, signer=signer,
                             out_dir=Path(a.out), deal=Deal(settlement="manual"),
                             dashboard_port=a.dashboard_port, feedback_port=a.feedback_port)
    except (NotReady, Cancelled, ValueError) as e:
        sys.exit(f"[run] {type(e).__name__}: {e}")
    print("\n[verify]")
    results = adapter.verify_episode_dir(ep.path, task.raw)
    for r in results:
        print(r.line())
    if ep.status != "completed" or not all(r.ok for r in results):
        sys.exit(f"[deliver] NOT packaged: the episode is not valid ({ep.path})")
    z = package_zip(ep.path, Path(a.deliveries))
    print(f"\n[deliver] {z}")
    if a.crow_delivery:
        doc, ct = build_delivery(ep.path, telemetry_url=f"file:{ep.path.name}.telemetry.enc",
                                 available_until=(dt.date.today() + dt.timedelta(days=30)).isoformat())
        (Path(a.deliveries) / f"{ep.path.name}.roaas-episode.json").write_bytes(doc)
        (Path(a.deliveries) / f"{ep.path.name}.telemetry.enc").write_bytes(ct)
        print(f"[deliver] {ep.path.name}.roaas-episode.json ({len(doc) / 1024:.0f} KB) + encrypted telemetry")


def cmd_verify_delivery(a):
    results = verify_delivery(
        Path(a.delivery).read_bytes(),
        expected_order=Path(a.order).read_bytes() if a.order else None,
        fingerprint=a.fingerprint,
        telemetry_ciphertext=Path(a.telemetry).read_bytes() if a.telemetry else None)
    for r in results:
        print(r.line())
    ok = all(r.ok for r in results)
    print("\nRESULT:", "delivery VALID" if ok else "delivery NOT valid")
    sys.exit(0 if ok else 1)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="roaas", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("validate")
    p.add_argument("order")
    p.add_argument("--limits")
    p.set_defaults(func=cmd_validate)
    p = sub.add_parser("probe")
    p.add_argument("--host", required=True)
    p.add_argument("--robot", default="dobot_cr3a")
    p.add_argument("--dashboard-port", type=int, default=29999)
    p.set_defaults(func=cmd_probe)
    p = sub.add_parser("run")
    p.add_argument("order")
    p.add_argument("--robot-info", required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--live", action="store_true")
    p.add_argument("--signer", choices=["software", "tpm"], default="software")
    p.add_argument("--keys-dir", default="keys")
    p.add_argument("--out", default="episodes")
    p.add_argument("--deliveries", default="deliveries")
    p.add_argument("--dashboard-port", type=int, default=29999)
    p.add_argument("--feedback-port", type=int, default=30004)
    p.add_argument("--crow-delivery", action="store_true", help="also write a roaas-episode/1 delivery (test)")
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("verify-delivery")
    p.add_argument("delivery")
    p.add_argument("--order")
    p.add_argument("--fingerprint")
    p.add_argument("--telemetry", help="the encrypted full telemetry downloaded from the delivery's URL")
    p.set_defaults(func=cmd_verify_delivery)
    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
