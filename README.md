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
|---|---|
| `kits/dobot_kit/` | The existing lab kit, v0.4.4, unchanged (the robot's private `robot_info_cr3a.json` is replaced by `robot_info_cr3a.example.json`) |
| `kits/dobot_kit_client/` | The buyer edition of the same kit, v0.4.4 |
| `docs/CROW_SELLER_REPORT.md` | How Crow's seller side works and what it means for this project |
| `docs/decisions.md` | Decision log |
| `docs/setup/` | Setup notes (lab mini PC network, later provider setup) |

## Safety rules

- A real robot moves only within the limits it publishes; out-of-limits jobs are rejected, never clamped.
- In the current phase every job needs an operator on site who types `ENABLE` at the lab PC.
- Rate limits, a maximum queue length, and a pause switch for the operator.
- Jobs are offered only while the robot is online and ready.
