# Crow files for ROaaS (generated)

**Do not edit these files by hand.** Regenerate them from the robot adapter, the lab's published limits and
`pricing.json`:

```bash
cd ~/dev/roaas
PYTHONPATH=provider python -m roaas.cli offers build --fingerprint <lab key sha256> --out crow \
    [--job-api https://<our job API>] [--payout 0x<payment address>]
PYTHONPATH=provider python -m roaas.cli offers build --fingerprint <lab key sha256> --out crow/rehearsal \
    --rehearsal --pricing crow/pricing.json
```

| File | What it is | Who uses it |
|---|---|---|
| `template.json` | The deal template: deliverable and the 11 checks, with `{{task}}`, `{{orderText}}`, `{{nonce}}` and `{{orderSha256}}` filled from each buyer's scope | Crow's seller adapter (hosted) |
| `seller.json` | The seller adapter's configuration: scope rules, our job API (still a placeholder), timing, payout address (still a placeholder) | Crow's seller adapter (hosted) |
| `offers.json` | Offer catalogue: per task, the order schema, limits, deliverable, price formula, how to order | Our site, the agent card |
| `pricing.json` | Prices and the delivery window: **placeholders to fill** | `offers build`, the job API |
| `example-terms.json` | The terms filled for `kits/dobot_kit/example_order.json` | Checking with Crow's tools |
| `rehearsal/` | The same, but the checks accept **simulator runs only** and the description says "REHEARSAL ONLY" | Rehearsals; never a real deal |

**Pinned key:** `0e811c503e02aa59faa288365e0f80023603ee9cfcd64f3ac4e67200960289af`, the software key on the
lab mini PC, as found in the real recordings of 2026-11-25. If the key changes (for example a TPM or a token),
rebuild everything and send buyers the new fingerprint out of band.

**Checking with Crow's own code** (a built clone of github.com/rafaeldavid/crow):

```bash
node examples/check-terms.ts <roaas>/crow/template.json
node examples/check-delivery.ts <terms.json> <delivery.json>     # terms from: roaas terms ORDER --template ...
```
