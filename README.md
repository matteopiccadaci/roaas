# ROaaS: Robot as a Service

A small, robot-agnostic provider that sells recorded robot work through
[Crow](https://crow.direct), an escrow service: the buyer's payment is locked, the delivery is checked
against agreed criteria, and the payment is released only when the checks pass.

The first robot is a DOBOT CR3A. Other robots (arms, mobile robots, sensors, lab instruments) are meant to
plug in later as adapters, without changing the core.

> **Status: testnet only.** Deals settle on Ethereum Sepolia in a test token with no value. No real funds,
> no mainnet keys.

## Layout

| Path | What it is |
| --- | --- |
| `kits/dobot_kit/` | The existing lab kit, v0.4.5, unchanged (the robot's private `robot_info_cr3a.json` is replaced by `robot_info_cr3a.example.json`) |
| `kits/dobot_kit_client/` | The buyer edition of the same kit, v0.4.5 |
| `provider/` | Python package `roaas`: core, CR3A adapter, Crow terms, lab agent (see its README) |
| `cloud/` | Job API on Cloudflare Workers (free plan): Worker and SQLite Durable Object, status page |
| `crow/` | Generated Crow deal template, seller adapter config, offer catalogue |
| `scripts/crow_sim_rehearsal.py` | A full deal on Crow's local simulator, with our Worker, the agent and the fake robot |
| `docs/ARCHITECTURE.md` | Proposed architecture: tiers, interfaces, schemas, checks, plan |
| `docs/CROW_SELLER_REPORT.md` | How Crow's seller side works and what it means for this project |
| `docs/decisions.md` | Decision log |
| `docs/questions-for-crow.md` | Open questions for the Crow team, with their answers |
| `docs/setup/` | Setup: lab mini PC network, Cloudflare deploy and lab agent (`cloudflare.md`) |

## Safety rules

- A real robot moves only within the limits it publishes; out-of-limits jobs are rejected, never clamped.
- In the current phase every job needs an operator on site who types `ENABLE` at the lab PC.
- Rate limits, a maximum queue length, and a pause switch for the operator.
- Jobs are offered only while the robot is online and ready.
