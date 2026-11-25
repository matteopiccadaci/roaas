# How Crow's seller side works (deliverable 1)

Written 2026-11-25. Sources:
- the public documentation at https://crow.direct (`llms.txt`, `setup.txt`, `sell.txt`, `reference.txt`, `/problems/`);
- the Crow repository https://github.com/rafaeldavid/crow at commit `1e6d0bc` (2026-11-25), read only. Nothing
  was installed or run, and no Crow API was called.

Items marked *(unverified)* come from summaries, not from the source text.

## 1. Verdict

The brief's understanding is right. We do **not** build a platform: we are a **seller**. Crow provides:
- the operators (they relay messages and pay the gas);
- the escrow contract on Sepolia;
- the adjudicators (they run the checks);
- the **seller adapter**, which speaks the protocol on our behalf.

We provide a small **job API** with three calls (`quote`, `start`, `result`) and the robot side behind it.

## 2. A deal, step by step

```
buyer ── POST /quote {buyer, buyerEncryptionPublicKey, scope} ──▶ seller adapter
                         adapter checks the scope, asks our API for a price, fills our deal template,
                         signs the offer (EIP-712), submits it ──▶ {commitmentId, price, fees, expiresAt}
buyer funds the hold (price + adjudication fee)                  state: Offered → Held
adapter polls the operators; when every operator shows Held ──▶ POST our /start   (Idempotency-Key = commitmentId)
adapter polls our GET /jobs/{id}/result: 202 while running, 200 with the file bytes
adapter runs the adjudicators' checks locally on those exact bytes (preflight)
   pass  → encrypts, uploads, signs the Delivery                 state: Delivered
   fail  → does not deliver, alerts us, waits for a fixed result until deliverBy
adjudicators run the checks: pass → Released (seller paid, buyer gets the key)
                             fail → Refunded (price back to buyer, no key)
```

| Deadline | Default | Meaning |
|---|---|---|
| `offerExpiresAt` | created + 900 s | the offer lapses if not funded |
| `fundBy` | created + 1800 s | the buyer must fund before this |
| `deliverBy` | created + `deliverWithinSec` (default 86 400 s; we set it in our quote) | the delivery must arrive before this |
| `judgeBy`, `fallbackBy` | after `deliverBy` | adjudicators' windows |

When a deadline is missed:
- **No delivery by `deliverBy`:** the buyer reclaims price **and** fee; the seller gets nothing.
- **A required check fails:** the price is refunded, but the adjudication fee is not.
- **After funding:** there is no unilateral seller decline. The seller can sign a full refund, which the
  buyer must co-sign (the signature is valid for 23 h). Otherwise the deadline does the job.

Phase one has no appeal: the first decision is final.

## 3. The seller adapter

**What it does:**
1. Validates the buyer's `scope` against declared rules.
2. Asks our API for a price.
3. Applies terms rules: per-scope windows, extra checks, refusals.
4. Signs and submits the offer.
5. Polls the operators.
6. Calls `start`.
7. Fetches the `result`.
8. Pre-runs the checks.
9. Encrypts and delivers.
10. Handles refunds.

It keeps a **journal**, so after a restart it neither starts nor delivers twice.

**Configuration:** one JSON file, no source edits. Only `sellerApi` is required. Useful fields:
- `quoteAuth` (bearer credentials per buyer);
- `quoteRateLimit`;
- `payoutAddress` (keep it apart from the signing key in production);
- `termsRules` (`refuse` text, `timing`, `checks`).

**Where it runs:**
- as a Node process with a journal file (`examples/seller-adapter.ts`), which needs a public HTTPS name for
  `/quote`;
- or as a **hosted seller**: Crow runs the same core (`seller-adapter-core.ts`) on its Cloudflare account, as
  one Worker plus one Durable Object per seller, with the card at `crow.direct/agents/<slug>` (ADR-0037,
  accepted 2026-11-14).

The hosted variant needs only our job API to be reachable on the internet. That is the plan (decision D3).

### Our job API contract

```
POST <quote>  {scope, options?}                 → {price: {token, amount}, quoteId?, deliverWithinSec?}
POST <start>  {commitmentId, quoteId, scope}    → {jobId}   (same Idempotency-Key ⇒ same job)
GET  <result> (jobId in the path)               → 202 running | 200 result bytes
```

`amount` is in base units, as a decimal string.

**Two constraints found in the code:**
- **`scope` fields can only be `string` (with a pattern), `strings` (distinct strings) or `integer`
  (min/max).** There are no arrays of numbers and no decimals. A list of waypoints therefore has to travel as
  an encoded string (for example the canonical order JSON, or `"12.5,-15,25,30,-40,45;…"`), validated by a
  pattern in the adapter and fully parsed by our API.
- **The adapter forwards only the status of our API's errors, not the body.** Detailed rejection reasons
  (for example "J3 = 50 outside [-45, 45]") will not reach the buyer through `quote`. Mitigations:
  - a public `validate` endpoint on our side, documented in the card;
  - `termsRules.refuse` texts for the cases the adapter can express;
  - asking Crow to pass a short reason through.

## 4. Offers and discovery

- There is **no central catalog**. A seller publishes an agent card: `<script type="application/json"
  id="crow-agent">` in a web page, or `agent.json`. It names the quote URL and `/defaults`. Schema:
  https://crow.direct/card.schema.json *(not read yet)*. Hosted sellers get their page at
  `crow.direct/agents/<slug>`.
- `/defaults` publishes the deal template, the windows and the scope rules, so a buyer can compare the
  terms of an offer with what we advertise.
- **Terms fields:**
  - `deliverable {kind, description, format}`;
  - `acceptance.deterministicChecks[]`, plus an empty rubric for `checks-only`;
  - `price {token, amount}`;
  - `adjudication {policyId, fee}`;
  - `timing {offerExpiresInSec, fundWithinSec, deliverWithinSec, judgeWithinSec, fallbackWithinSec,
    appealWindowSec = 0, appealDecideSec, maxSubmissions = 1}`;
  - `settlement`, `remedy {onReject: full_refund}`, `disclosure {level: public}`.
- **Numbers inside the terms must be integers** (ADR-0022). Check bounds therefore use whole degrees, or
  fixed-point integers such as millidegrees.

## 5. Checks

All checks are **declarative**, evaluated by built-in code on the delivered bytes only. A linked file is
never fetched. The kinds are:

| Kind | Purpose |
|---|---|
| `json_schema` | A subset of JSON Schema: `type`, `enum`, `const`, `properties`, `required`, `additionalProperties`, `items`, `min/maxItems`, `uniqueItems`, `minimum`/`maximum` (also exclusive), `min/maxLength`, `pattern`, `anyOf`/`allOf`/`oneOf`/`not`. **No** `$ref`, `prefixItems`, `contains`, `if/then`, `multipleOf`, `format` |
| `csv_columns_exact`, `row_count_range`, `unique_key`, `null_rate_max`, `regex_match_rate_min`, `iso8601_date_column` | CSV structure and content |
| `hidden_characters_max` | Control and invisible characters |
| `reference_row_match_min` | Rows compared with a reference file uploaded to the operators |

Limits: 1 to 10 checks per deal, at least one required; patterns are full matches of at most 200
characters; there is no numeric range check for CSV (ranges are written as regexes).

**Relevant work already in the Crow repository:**
- **`examples/robot-episode.ts`.** A synthetic `dobot_cr3a` episode at 125 Hz, delivered as **one JSON
  document** (LeRobot v3 layout plus a provenance block), with ten checks:
  - format and metadata constants;
  - frame counts and joint bounds;
  - `const` nonce and waypoints;
  - each arrival within ±1° of its waypoint;
  - the pinned device public key, with a pattern on the signed message;
  - no hidden characters.
- **`fixtures/terms/telemetry-run.yaml`.** A telemetry CSV with integer fixed-point columns, whose
  completeness is proven by `row_count_range`, a timestamp-grid regex and `unique_key`.
- **ADR-0045, `signature_valid`** (*proposed* on 2026-11-25, design only, no code yet). It verifies an
  ECDSA P-256 or Ed25519 signature over the **exact bytes** of a string in the delivered JSON (for example
  `/manifest_text`), with the key's SPKI SHA-256 pinned in the terms. `jsonEquals: /manifest` ties the
  signed bytes to the object that the other checks read. The ADR uses **our kit's manifest as its worked
  example**. Until the adjudicators run it, an offer that names it is declined.

**Consequence for us.** Today the checks can prove that the delivery is well formed and that it matches
the order: nonce, waypoints, arrivals, bounds, pinned key, status. They cannot prove that our signature is
valid. The buyer proves that offline after the key is released, when payment is already final. Once
`signature_valid` ships, the signature is checked **before** payment. We should design the delivery so
that it already fits that check.

## 6. Delivery

- **One file per deal** (`maxSubmissions = 1`). The documented formats are JSON (`application/json`) and
  CSV (`text/csv`); a zip is not documented.
- **Size: at most 1 048 576 bytes of ciphertext** (`limits.maxCiphertextBytes` on the current nodes). A
  larger upload gets `413 payload_too_large`.
- **Encryption:**
  - the content is encrypted with AES-256-GCM and a fresh key;
  - that key is sealed with NaCl box, once for each of the three adjudicators and once for the buyer;
  - the buyer can open it only after the deal is Released.
- **Who sees the content:** the three adjudicators decrypt the whole file. With a rubric (AI) template, the
  work would also be sent to model providers (Anthropic, DeepSeek), and the public judgments may quote it.
  We use `checks-only`.

## 7. Money and keys

- **Price:** paid in Crow Test USDC (6 decimals). The **adjudication fee is paid by the buyer** on top of
  the price. The operator fee is 0 on the current nodes. The operators pay the gas.
- **Seller key:** a secp256k1 key. It signs offers and deliveries with EIP-712, domain
  `Holdback v0.8`, chain 11155111.
  - The Node adapter reads it from `HOLDBACK_KEY`, or uses a separate signer through `CROW_SIGNER_URL`
    (`examples/signer.ts`).
  - Hosted sellers keep it as a Worker secret.
  - No hardware-wallet support is documented, and an automatic adapter must sign without a human anyway.
    `payoutAddress` lets the income go to a different, colder address.
- **Mandate:** offering needs a mandate in the club. On the test service the adapter obtains it from the
  faucet and renews it every 30 days.

## 8. Measured: recording size versus the 1 MiB limit

The kit v0.4.4 was run against `fake_dobot.py` on macOS. All four episodes were VALID.

| Order | Duration | CSV | CSV gz | gz + base64 |
|---|---|---|---|---|
| 10 waypoints, 100% | 10.8 s | 350 KB | 135 KB | 180 KB |
| 3 waypoints, 30% (`example_order.json`) | 16.7 s | 535 KB | 208 KB | 277 KB |
| 10 waypoints, 30% | 30.0 s | 955 KB | 374 KB | 498 KB |
| 10 waypoints, 10% | 84.8 s | 2 688 KB | 1 030 KB | **1 373 KB: does not fit** |

That is about 32 KB/s for plain CSV and 16.5 KB/s for gz + base64. So 1 MiB holds about **30 s of plain
CSV, or about 60 s of gz + base64**. The fake robot's data may compress better than real data; this needs
re-measuring on a real CR3A recording. Note also that `episode.py` aborts a motion after 90 s (`--timeout`).

How to remove the duration limit is an open decision; the proposal is in the chat of 2026-11-25.

## 9. Differences from the brief

| Brief | Reality |
|---|---|
| "Checks that reuse our verifier" | Checks are declarative and cannot run our verifier. Verifier results become `json_schema` constraints; the signature check arrives with `signature_valid` |
| Delivery = our zip | One JSON or CSV file of at most 1 MiB |
| Validate, then queue for approval | Validation happens at **quote time**, before funding. A funded job cannot be declined unilaterally |
| "Who pays the adjudication fee" | The buyer, by protocol |
| Hardware wallet for the Crow key | Not supported; a hot key plus a separate `payoutAddress` instead |
| Task schema with nested params | `scope` holds only strings and integers; the task is encoded as a string |

## 10. What we need to run

- **On our side:** a public HTTPS job API (Vercel), plus the lab agent on the mini PC (outbound only).
- **From Crow:** a hosted seller (the adapter, the agent card page and the Worker secrets), configured
  with:
  - our deal template and terms rules;
  - our job API URLs;
  - quote credentials and a rate limit;
  - the payout address.
- **For local rehearsal (Mac):** Node ≥ 22.18 and pnpm, then `pnpm install && pnpm build`, `pnpm sim`,
  `node examples/seller-adapter.ts … --sim`, and `node examples/check-delivery.ts terms.json work.json`.
  This needs explicit approval, because it installs third-party npm packages.

## 11. Still to verify

- the card schema (`card.schema.json`);
- the media-type allow-list;
- adjudicator data retention;
- the last part of `reference.txt` (`seller-adapter`, `limits`);
- whether hosted sellers can use `CROW_SIGNER_URL`;
- when `signature_valid` will be built;
- whether the seller API's refusal reason can be passed through to the buyer.

These are good questions for Crow.
