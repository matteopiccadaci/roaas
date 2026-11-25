# Decision log

Newest last. "Open" items are waiting for a decision.

## 2026-11-25

**D1. Name: ROaaS.** "RaaS" was dropped: in security it usually means *Ransomware-as-a-Service*.
"ROAAS" is also an aviation acronym (Runway Overrun Awareness and Alerting System). That is a different
field, so the clash is acceptable. No trademark search was done.

**D2. Testnet only.** Sepolia and Crow Test USDC. No institutional approval is needed for the testnet
demo. Before any real-value use, the following must be cleared:

- use of the lab's robot by the lab head or supervisor;
- running jobs for outside parties, and unattended mode, with the university's safety office;
- network use of the lab PC with IT;
- commercial activity "conto terzi" (invoicing, VAT, receiving crypto as a public body);
- compatibility with the PhD scholarship.

**D3. Hosting.**

- **Crow seller adapter:** hosted by Crow on Cloudflare (hosted sellers, Crow ADR-0037). This is assumed
  while the confirmation from Crow is pending. Fallback 1: our own Cloudflare Worker using Crow's
  `apps/hosted-sellers`. Fallback 2: the Node adapter behind a tunnel.
- **Public job API and site:** Vercel. The Hobby plan is for non-commercial use only; real sales need Pro.
- **Lab agent:** runs on the lab mini PC and makes outbound connections only. No open ports, no VPN, no
  tunnel. It re-validates every job against its local limits and does not trust the cloud.

**D4. Checks: deterministic only.** Template `checks-only`, no AI rubric. Adopt Crow's `signature_valid`
check (ADR-0045, proposed 2026-11-25) as soon as the adjudicators run it.

**D5. Approval policy.** It is configurable.

- Now: `on-site`. The operator is notified by email and types `ENABLE` at the mini PC console.
- Later: `auto`. According to the lab, the cell's physical safety measures are already in place; only the
  camera is missing. Switching to `auto` also requires revising the project brief, which today calls
  operator approval non-negotiable.

**D6. Keys (testnet).** One new secp256k1 key, used only for this project. It is stored as a secret of the
service that hosts the adapter, with an encrypted backup protected by a passphrase. Payout goes to the
same address for now.

**Required before any real-value deployment:**

- the hot signing key is kept separate from the payout address (`payoutAddress`), which lives on a
  hardware wallet;
- a separate signer (`CROW_SIGNER_URL`) where possible;
- a fresh key, never reused from testnet;
- a written plan for key rotation and for a compromised key.

**D7. Privacy of the delivery.**

- `recorder.target_host` and `recorder.host_os` are removed.
- `serial_hmac_sha256` is kept, which requires `DOBOT_SERIAL_SALT` on the lab PC.
- The buyer's nonce and waypoints become public in the deal terms. This is acceptable: it reveals what was
  ordered and creates no security risk.

**D8. The deal is bound into the signed manifest.** The manifest carries `commitmentId` and the buyer's
address (manifest schema `dobot-episode/0.6`). The verifier stays backwards compatible.

**D9. Online gating.** The lab agent sends a heartbeat with the robot's status. Quotes are refused when:

- the last heartbeat is older than 30 s;
- the robot is not ready (an alarm is active, or it is not in TCP mode);
- (on-site phase) the operator is off duty.

**D10. Manual path kept.** Orders by email or Telegram still work. `run_order.py` keeps working as a
fallback, and manual orders can enter the same queue (`--manual`) so that the robot runs one job at a time.

**D11. Offers.** Two very simple templates. Prices and deadlines are placeholders.
`max_speed_factor` stays at 100; lowering it for unknown buyers is a possible future change.

**D12. Notifications** to the operator by email.

**D13. Language.** Code and docs in English.

**D14. Two-level delivery (option A)** removes the about-60-s limit set by Crow's 1 MiB cap.

- **The Crow delivery (at most 1 MiB)** holds:
  - the signed manifest and a summary (status, arrivals at each waypoint, violations);
  - a telemetry preview in **plain JSON**, so that the checks can read it: time, positions and speeds,
    downsampled just enough to fit. Measured on the real CR3A: at 125 Hz the preview fits about 1.6 min,
    at 25 Hz about 8 min;
  - a link, a random decryption key and the SHA-256 of the full 125 Hz telemetry.
- **The full telemetry** is stored encrypted by us (Vercel Blob). The buyer gets the key only when the deal
  is released, and the hash is already in the signed manifest.
- **The trade-off:** the availability of the full file depends on us and is not held in escrow, so we
  commit to keeping it for N days (placeholder).
- **Follow-ups:**
  - a more compact encoding (option B) is a later optimisation;
  - a larger limit is asked of Crow (`questions-for-crow.md`, Q3);
  - the kit's fixed 90 s motion timeout becomes derived from the estimated duration.

**D15. Wired robot link in place (kit v0.4.5).**
- The controller's LAN1 is `192.168.5.1`.
- The mini PC's Ethernet is `192.168.5.100/24`, with no gateway and no DNS; internet goes over the PC's
  Wi-Fi.
- The first real recording over the cable has no resyncs and no gaps (`CROW_SELLER_REPORT.md`, section 8).

**D16. The robot must start disabled.** The lab agent starts a job only when:
- the dashboard answers, which means TCP mode is on and DobotStudio Pro is not holding the robot in online
  mode;
- `RobotMode` is 4, "disabled";
- there are no alarms.

Otherwise the robot is reported as not ready, and quotes are refused. Context: in the first real recording the
arm was already enabled (mode 5) before the operator confirmed.

**D17. Robot metadata.**
- Fill in the real factory `calibration_date` in the private `robot_info_cr3a.json` on the mini PC.
- If DobotStudio Pro allows it, set the controller clock, which is about 7 h off.
- Neither blocks the work: absolute time comes from the PC's NTP clock.

**D18. Crow's one-run pilot coexists with ROaaS.** Rafa's setup page "Sell one robot run through Crow"
describes a pilot run entirely on the robot PC:
- `crow_bridge.py` on 127.0.0.1:8790;
- Crow's `serve.mjs` adapter on 127.0.0.1:8791;
- the offer is made by the seller, with Crow's order and Crow's buyer agent, and coordinated with Rafa on
  Telegram;
- the telemetry is gzipped inside one JSON file of at most 1 MiB.

It uses the same kit, the same rules (the arm moves only once the payment is locked, and only after the
operator's `ENABLE`) and the same kind of signed bundle, so it is a valid first step. ROaaS generalises it
into an always-on service: a Vercel job API, a hosted adapter, an outbound-only lab agent and two-level
deliveries.

To keep the pilot intact, ROaaS never edits the kit on the robot PC (`C:\dobot_kit`): its code lives in
`provider/`.

Fact learnt from the page: the operators refuse `deliverWithinSec` ≤ 7200 s (`fundBy < deliverBy`), so
every deal's delivery window must exceed 2 h, even in `auto` mode.

**D19. Phase 4: the checks use Crow's new proof kinds** (added to Crow on 2026-11-25:
`signature_valid`, `equal_lengths`, `monotonic_column`, `passes_through`).

- **The checkable preview moves inside the signed manifest,** as integer series (ms, millidegrees,
  millidegrees per second), with every arrival sample kept. `signature_valid` with `jsonEquals: /manifest`
  ties every series the other checks read to the lab's key; a hash alone would not tie them.
- **`recorder.simulator`** replaces `target_host` for the "not a simulator" check.
- **Rehearsal terms** (`crow/rehearsal/`) accept simulator runs only, as in Crow's pilot bridge.
- **Scope fields:** `task`, `orderText`, `nonce`, `orderSha256`, the pilot bridge's fields plus `task`.
- **`passes_through`** against the buyer's waypoints waits for per-deal integer waypoints in the terms (Q11).
- **Verified offline with Crow's adjudicator code** (commit `423ffcd`): good deliveries are accepted and every
  tampering is rejected (ARCHITECTURE §4.5).

**D20. Cloudflare instead of Vercel** (phase 5, on advice received by the lab). The job API is a Cloudflare
Worker on the free plan, with one SQLite Durable Object per robot.

- **The Durable Object holds** the quotes, the job queue, the robot's status, and the files (deliveries and
  encrypted telemetry, in 1 MB chunks, deleted after the retention period). There is no R2, which might need
  a payment method.
- **The lab agent connects OUT over one WebSocket** (WebSocket hibernation, free while idle). It validates
  every quote with the lab's own Python rules, so the rules have one source; it receives jobs and uploads
  results over HTTPS.
- **"Robot online"** means: the socket is connected, there is a fresh status, the robot is ready (D16) and
  the operator is on duty.
- **At start, the Worker reads from Crow's operators** that the hold is funded, the real `deliverBy`, the
  buyer's address, and that the terms name the quoted order (`orderSha256`).
- **The quote rate limit is global:** the seller adapter does not pass the buyer to `quote`.
- **Verified locally:** with `wrangler dev`, the lab agent and the fake robot (`tests/test_cloud_local.py`).
  A full deal on Crow's simulator (`scripts/crow_sim_rehearsal.py`), with Crow's own seller adapter and buyer,
  ends **Released**.

Open questions for Crow are tracked in `questions-for-crow.md`.
