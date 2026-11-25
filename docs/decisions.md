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

Open questions for Crow are tracked in `questions-for-crow.md`.
