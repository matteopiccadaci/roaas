"""ROaaS command line (phase 3: lab-side commands; queue and agent commands come in phases 5-6).

  roaas validate ORDER [--limits FILE]          parse, validate, estimate, print the program
  roaas probe --host IP                         read-only robot status (ready = disabled, no alarm)
  roaas run ORDER --robot-info FILE [--host IP --live] [--crow-delivery]
                                                manual path: approve, run, record, sign, verify, zip
  roaas verify-delivery DOC [--order FILE --fingerprint HEX --telemetry FILE]
  roaas offers build --fingerprint HEX [--job-api URL --payout 0x.. --pricing FILE --out DIR]
                                                Crow deal template, seller adapter config, offer catalogue
  roaas scope ORDER                             the Crow scope a buyer sends for this order
  roaas terms ORDER --template FILE             the filled deal terms for this order (for check-delivery.ts)
  roaas agent --cloud URL --robot-info FILE [--host IP --live]
                                                the lab agent: connects out to the job API, runs approved jobs

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
        doc, ct = build_delivery(ep.path, telemetry_url=a.telemetry_url or f"file:{ep.path.name}.telemetry.enc",
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


DEFAULT_PRICING = {
    "token": "PLACEHOLDER: Crow Test USDC token address on Sepolia",
    "deliver_within_sec": 259200,
    "joint_waypoints": {"formula": "base + per_waypoint x waypoints", "base": "PLACEHOLDER", "per_waypoint": "PLACEHOLDER"},
    "repeatability": {"formula": "base + per_waypoint x waypoints x repetitions", "base": "PLACEHOLDER",
                      "per_waypoint": "PLACEHOLDER"},
}


def cmd_offers(a):
    from .settlement import crow_terms as ct

    adapter = get_adapter(a.robot)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pricing_path = Path(a.pricing) if a.pricing else out / "pricing.json"
    if not pricing_path.exists():
        pricing_path.write_text(ct.dumps(DEFAULT_PRICING))
        print(f"[offers] wrote placeholder prices to {pricing_path}: fill them in")
    pricing = json.loads(pricing_path.read_text())
    tmpl = ct.template(adapter, a.fingerprint.lower(), rehearsal=a.rehearsal)
    bad = ct.floats_in(tmpl)
    if bad:
        sys.exit(f"[offers] the template holds non-integer numbers (Crow ADR-0022): {bad[:5]}")
    cfg = ct.adapter_config(a.job_api, a.payout, {"deliverWithinSec": int(pricing.get("deliver_within_sec", 259200))})
    (out / "template.json").write_text(ct.dumps(tmpl))
    (out / "seller.json").write_text(ct.dumps(cfg))
    (out / "offers.json").write_text(ct.dumps(ct.offers_catalog(adapter, pricing, a.job_api)))
    print(f"[offers] wrote {out / 'template.json'} ({len(tmpl['checks'])} checks), {out / 'seller.json'}, "
          f"{out / 'offers.json'} ({len(adapter.task_types())} offers)")


def cmd_scope(a):
    from .settlement import crow_terms as ct
    print(json.dumps(ct.scope_for_order(Path(a.order).read_bytes()), indent=2))


def cmd_terms(a):
    from .settlement import crow_terms as ct
    terms = ct.terms_for(json.loads(Path(a.template).read_text()), ct.scope_for_order(Path(a.order).read_bytes()))
    text = ct.dumps(terms)
    if a.out:
        Path(a.out).write_text(text)
        print(f"[terms] wrote {a.out}")
    else:
        print(text)


def cmd_agent(a):
    import os
    from .agent.agent import LabAgent
    from .robots.dobot_cr3a.kit import signers

    token = os.environ.get(a.token_env, "")
    if not token:
        sys.exit(f"[agent] set {a.token_env} to the lab token (never put it in a file you share)")
    if not approvals.is_local_host(a.host) and not a.live:
        sys.exit("[agent] a robot that is not on this machine needs --live (the operator types ENABLE for each job)")
    agent = LabAgent(cloud=a.cloud, token=token, robot_info=json.loads(Path(a.robot_info).read_text(encoding="utf-8")),
                     host=a.host, approval=approvals.OnSiteConsole() if a.live else approvals.SimulatorOnly(),
                     signer=signers.make_signer(a.signer, a.keys_dir), out_dir=Path(a.out),
                     dashboard_port=a.dashboard_port, feedback_port=a.feedback_port, on_duty=not a.off_duty)
    try:
        agent.run()
    except KeyboardInterrupt:
        agent.stop.set()
        print("\n[agent] stopped")


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
    p.add_argument("--telemetry-url", help="where the encrypted full telemetry will be served (https://...)")
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("verify-delivery")
    p.add_argument("delivery")
    p.add_argument("--order")
    p.add_argument("--fingerprint")
    p.add_argument("--telemetry", help="the encrypted full telemetry downloaded from the delivery's URL")
    p.set_defaults(func=cmd_verify_delivery)
    p = sub.add_parser("offers")
    p.add_argument("action", choices=["build"])
    p.add_argument("--robot", default="dobot_cr3a")
    p.add_argument("--fingerprint", required=True, help="SHA-256 hex of the lab key (printed after every run)")
    p.add_argument("--job-api", default="https://PLACEHOLDER.vercel.app", help="base URL of the ROaaS job API")
    p.add_argument("--payout", default="PLACEHOLDER", help="0x address that receives payments")
    p.add_argument("--pricing", help="pricing JSON (default: <out>/pricing.json, created with placeholders)")
    p.add_argument("--out", default="crow")
    p.add_argument("--rehearsal", action="store_true", help="terms for simulator runs only (never for a deal)")
    p.set_defaults(func=cmd_offers)
    p = sub.add_parser("scope")
    p.add_argument("order")
    p.set_defaults(func=cmd_scope)
    p = sub.add_parser("terms")
    p.add_argument("order")
    p.add_argument("--template", required=True)
    p.add_argument("--out")
    p.set_defaults(func=cmd_terms)
    p = sub.add_parser("agent")
    p.add_argument("--cloud", required=True, help="base URL of the ROaaS job API, e.g. https://roaas.<you>.workers.dev")
    p.add_argument("--robot-info", required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--live", action="store_true")
    p.add_argument("--signer", choices=["software", "tpm"], default="software")
    p.add_argument("--keys-dir", default="keys")
    p.add_argument("--out", default="episodes")
    p.add_argument("--dashboard-port", type=int, default=29999)
    p.add_argument("--feedback-port", type=int, default=30004)
    p.add_argument("--token-env", default="ROAAS_LAB_TOKEN")
    p.add_argument("--off-duty", action="store_true", help="connect, but accept no quotes")
    p.set_defaults(func=cmd_agent)
    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
