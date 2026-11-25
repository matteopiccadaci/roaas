# Questions for Crow (Rafael)

Open questions for the Crow team. When an answer arrives, fill in **Answer** and **Date**, set **Status**
to `answered`, and note in `decisions.md` any decision it changes.

Status values: `open`, `asked`, `answered`, `dropped`.

---

## Q1. Hosting the ROaaS seller adapter

- **Question:** Can Crow host the ROaaS seller adapter as a hosted seller (ADR-0037)?
  - Which slug should we use, for example `crow.direct/agents/roaas`?
  - What configuration do you need from us (deal template, terms rules, job API URLs, payout address)?
  - Can the selling key stay with us through `CROW_SIGNER_URL`, or must it be a Worker secret in your
    account?
- **Why:** The whole deployment plan assumes this (decision D3). The fallback is our own Cloudflare Worker.
- **Status:** assumed yes (the lab is in contact with Rafael); details to agree
- **Answer:** What we will send once our Worker is deployed: `crow/seller.json` and `crow/template.json` (with the job API's URL filled in), and the value of `ROAAS_JOB_API_KEY` through a private channel. The payout address is left out, so payments go to the signing key, unless we give one. The configuration was tested with Crow's own `seller-adapter.ts --sim` against our Worker: deal Released (D20).
- **Date:**

## Q2. When will `signature_valid` be built?

- **Question:** ADR-0045 (proposed 2026-11-25) uses our kit's manifest as its worked example. When will
  the adjudicators run this check?
- **Why:** With it, the ECDSA signature is verified before payment, not only by the buyer afterwards. We
  will shape our delivery for it from the start.
- **Status:** partly answered (from the repository)
- **Answer:** Implemented in the repository at commit `423ffcd` (packages/judgment/src/checks/signature-valid.ts), documented in site/ref/checks.txt, together with `equal_lengths`, `monotonic_column` and `passes_through`. ROaaS uses it (decision D19); Crow's own check-delivery.ts verifies our signature. **Still to confirm:** that the three testnet adjudicators list these kinds in `checkKinds` (an adjudicator lacking a kind declines the offer).
- **Date:** 2026-11-25

## Q3. Delivery size above 1 MiB

- **Question:** Can `limits.maxCiphertextBytes` be raised for us, or will deliveries with several files or
  large attachments be supported?
  - Our plan in the meantime: a delivery of at most 1 MiB that holds the signed manifest, a summary and a
    downsampled preview, plus a link, a decryption key and a hash for the full 125 Hz telemetry stored by us.
  - Is that pattern acceptable to Crow?
- **Why:** Telemetry is about 16.5 KB/s (gzip + base64), so 1 MiB holds only about 60 s at full rate
  (decision D14).
- **Status:** open
- **Answer:**
- **Date:**

## Q4. Passing the refusal reason through at quote time

- **Question:** When our job API refuses a quote (for example a joint outside the envelope, or the robot
  offline), can the adapter pass a short reason to the buyer instead of only the status code?
- **Why:** Today the buyer agent cannot tell what to fix. The workaround is a public `validate` endpoint.
- **Status:** open
- **Answer:**
- **Date:**

## Q5. Scope types for waypoints

- **Question:** Adapter `scope` fields accept only `string`, `strings` and `integer`. Will lists of numbers
  (or decimals) be supported? If not, which encoding do you recommend for a list of 6-joint waypoints in
  degrees: a canonical JSON string, or integers in millidegrees?
- **Why:** It decides the buyer-facing task format and the pattern rules.
- **Status:** open
- **Answer:**
- **Date:**

## Q6. Allowed media types

- **Question:** Which delivery media types are allowed? Is `application/json` the right choice for one
  episode document?
- **Why:** Problem type `mime_not_allowed`; the allow-list is not documented.
- **Status:** open
- **Answer:**
- **Date:**

## Q7. What the adjudicators keep

- **Question:** How long do the adjudicators, and the operators' ciphertext store, keep a delivery? Who can
  read it later?
- **Why:** It decides what we may put in a delivery (privacy, decision D7).
- **Status:** open
- **Answer:**
- **Date:**

## Q8. Seller-side cancellation after funding

- **Question:** If the robot breaks after a deal is funded, can the seller cancel with a full refund to the
  buyer, fee included, without the buyer's co-signature? Today we can only sign a refund and wait, or let
  `deliverBy` pass.
- **Why:** It decides the operator's "reject" action and the deadlines we quote.
- **Status:** open
- **Answer:**
- **Date:**

## Q9. Discovery

- **Question:** How do buyer agents find hosted sellers? Is there a listing, or only the card page? Is
  `card.schema.json` stable?
- **Why:** Offer templates must be published where agents look.
- **Status:** open
- **Answer:**
- **Date:**

## Q10. From testnet to real use (later)

- **Question:** What would a real-value setup require: club membership, mandates, adjudicators, contract
  audit status?
- **Why:** For planning only. ROaaS stays on testnet until the lab decides otherwise (decision D2).
- **Status:** open
- **Answer:**
- **Date:**

## Q11. Checks that depend on the deal

- **Question:** A hosted seller's deal checks come from a static template, plus `termsRules` and `{{scope}}`
  placeholders. A placeholder can insert a scope value as a whole value or as text, but it cannot compute
  anything with it (for example "waypoint ± 1°"). Could the seller API's `quote` answer return extra checks
  for that one deal? Or could placeholders accept numeric lists?
- **Why:** Today a check can pin the buyer's exact order text (`const "{{order}}"`), but it cannot check each
  arrival against its own waypoint. That check stays seller-declared (covered by the signature) and is
  verified by the buyer offline.
- **Status:** open (narrowed)
- **Answer:** `passes_through` now exists and checks a series against waypoints in the terms. To use it with the BUYER's waypoints, the hosted adapter would need per-deal integer lists: a scope rule type such as a list of integer lists (millidegrees), filled by a whole `{{waypointsMdeg}}` placeholder, or extra checks returned by our `quote`. Ask which Crow prefers.
- **Date:** 2026-11-25

## Q12. Telling a real arm from the simulator

- **Question:** The pilot bridge's checks read `recorder.target_host` and refuse a simulator address
  (`127.0.0.1`, `localhost`, `::1`). ROaaS manifest 0.6 removed `target_host` for privacy (D7). Should we keep
  it (a private LAN address such as `192.168.5.1` reveals little), or replace it with an explicit signed
  field such as `recorder.simulator: false`?
- **Why:** It decides whether ROaaS deliveries can pass the same check as the pilot.
- **Status:** open (we chose a proposal)
- **Answer:** ROaaS manifests carry a signed `recorder.simulator` (true only for a robot on the lab PC itself) and our checks require it to be false (true in rehearsal terms). Ask whether Crow is fine with this instead of `target_host`.
- **Date:** 2026-11-25
