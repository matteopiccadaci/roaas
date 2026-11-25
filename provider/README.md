# `roaas` provider package

The Python side of ROaaS: the robot-independent core and the robot adapters, used by the lab agent (mini PC)
and by the job API (Vercel). The design is in [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).

Requirements: Python 3.9 or later and `cryptography`. Tested on 3.9, 3.10 and 3.14.

```bash
cd provider
python -m venv .venv && source .venv/bin/activate       # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[test]"
pytest                                                  # starts the kit's fake controller on free ports
CROW_REPO=<built Crow clone> pytest                     # also runs Crow's adjudicator code on a delivery
ROAAS_CLOUD_E2E=1 pytest tests/test_cloud_local.py      # the real Worker under wrangler dev (npm install in cloud/)
```

## Commands

| Command | What it does |
|---|---|
| `roaas validate ORDER [--limits FILE]` | Parses an order (new `roaas-task/1` or a legacy CR3A order file), validates it, prints the estimate and the program |
| `roaas probe --host IP` | Read-only robot status. *Ready* means: dashboard answers, robot disabled (mode 4), no alarm |
| `roaas run ORDER --robot-info FILE [--host IP --live] [--crow-delivery]` | Manual path: approval, run, record, sign, verify, zip in the kit's layout. With `--crow-delivery`, it also writes a `roaas-episode/1` document and the encrypted full telemetry |
| `roaas verify-delivery DOC [--order FILE --fingerprint HEX --telemetry FILE]` | Buyer-side check of a `roaas-episode/1` delivery: the document alone, or with the full telemetry |
| `roaas offers build --fingerprint HEX [--out crow] [--rehearsal]` | Generates the Crow deal template, the seller adapter configuration and the offer catalogue (see `crow/README.md`) |
| `roaas scope ORDER` | The Crow scope a buyer sends for an order |
| `roaas agent --cloud URL --robot-info FILE [--host IP --live]` | The lab agent: one outbound WebSocket to the job API; validates quotes with the lab's rules, runs approved jobs, uploads results (`ROAAS_LAB_TOKEN` in the environment; needs `pip install -e ".[agent]"`) |
| `roaas terms ORDER --template FILE` | The deal terms filled for that order, for Crow's `check-delivery.ts` |

`--live` is required for any robot that is not on this machine. The operator then types `ENABLE`. Without
`--live`, only a local fake controller is accepted.

## What changes from the kit (v0.4.5)

- **The kit modules are copied byte for byte** into `roaas/robots/dobot_cr3a/kit/`; a test guards the copy.
  The kit on the robot PC is never edited.
- **Before anything moves:** no alarm, the dashboard answers, the robot is **disabled** (D16). The approval
  comes first, then the recorder starts, so the operator's waiting time is no longer recorded.
- **Manifest `dobot-episode/0.6`:**
  - removed: `target_host`, `host_os`, `python`;
  - added: `deal`, `task`, `program.targets_deg`, a signed `summary` (arrivals, violations, checks) and the
    hash of `preview.json`.
- **Motion timeout:** computed from the duration estimate (calibrated on the real CR3A) instead of a fixed
  90 s.
- **The kit's `verify_episode.py` accepts 0.6 episodes.** With a new-format order it can check `--nonce` only,
  because it reads `waypoints` at the top level of the order. Legacy orders keep the full `--order` check.
