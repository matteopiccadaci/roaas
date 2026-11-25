# Buyer guide — start here

This kit lets you order a recording from our DOBOT CR3A robot arm and check, on your own computer, that the
data you receive is genuine, new and unmodified. You never connect to the robot: you send us a small JSON file,
we run it and send you back a signed zip.

If you work with an AI assistant, give it this folder: this README, `ORDER_FORMAT.md` and `SECURITY.md`
describe everything (`LAB_README.md` shows what we run on our side).

## Current status

| Item | Now |
|---|---|
| Signing key | **Software key (interim)**. A TPM-backed key is implemented and will be used once a PC dedicated to the robot is installed; we will tell you, and send the new key fingerprint |
| Speed | any value from 1 to **100%**; we suggest **30%** as a cautious default. It is set per order (`speed_factor`), so you can change it from one order to the next |
| Allowed joint range | see `lab_limits_cr3a.json`; any combination inside it is allowed |
| One order | = one recorded run: home → your waypoints → home |
| Raw controller stream | kept by us; its SHA-256 is in every signed manifest, available for audit |

## Which device?

- **Writing an order**: any device. An order is a plain text JSON file: any text editor on any computer,
  tablet or phone works, and so does an AI assistant. We validate every order before the robot moves,
  so a wrong file is simply rejected, never executed.
- **Checking an order before sending and verifying deliveries**: any computer with Python 3.9 or newer
  (Windows, macOS or Linux). No TPM, no special hardware, no connection to our lab.
- Without Python, the signature alone can be checked with OpenSSL (commands in `SECURITY.md`, section 2),
  but only `verify_episode.py` runs all the checks.

## 1. Install (once)

You need Python 3.9 or newer. No robot, no network access to our lab.

Windows (PowerShell), in the kit folder:
```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
pip install cryptography
```
macOS / Linux:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install cryptography
```
Every later command runs in the kit folder with the environment active (re-run the activation line in each new terminal).

## 2. Rehearse the whole loop locally (optional, 5 minutes)

The kit includes a fake robot, so you can see exactly what we do and what you receive.

Terminal 1:
```bash
python fake_dobot.py
```
Terminal 2:
```bash
python run_order.py --order example_order.json --robot-info lab_limits_cr3a.json
```
This validates the example order, "runs" it on the fake robot, signs it with a local test key, verifies it
and writes a delivery zip into `deliveries/` — the same kind of zip we will send you. Stop the fake robot with Ctrl+C.

## 3. Create your order

Either generate one (random waypoints inside our limits, fresh 128-bit nonce, current timestamp):
```bash
python make_order.py --limits lab_limits_cr3a.json --points 3 --speed 30 --out my_order.json
```
or copy `example_order.json`, change `order_id`, write your own waypoints and a fresh nonce:
```bash
python -c "import secrets; print(secrets.token_hex(16))"
```
Rules in short (details in `ORDER_FORMAT.md`):
- `order_id`: your reference, letters/digits/`_ . -`, max 64 characters;
- `nonce`: 32 random hex characters, **new for every order**;
- `speed_factor`: integer, 1 to 100 (30 suggested);
- `waypoints`: 1 to 10 points, each `[J1, J2, J3, J4, J5, J6]` in degrees, inside `allowed_envelope_deg`;
- `issued_at_utc` (recommended): when you issue the order, e.g. `"2026-11-18T10:00:00Z"`.

## 4. Check it

```bash
python check_order.py my_order.json --limits lab_limits_cr3a.json
```
It must print `Order ... OK`. It applies exactly the checks we apply before running.

## 5. Send it

Email the `.json` file, as an attachment, to your contact at the lab. One file per order; several orders
can be sent at once if each has its own `order_id` and nonce.

**Keep your copy of the file exactly as sent**: verification compares its bytes with what we executed.

## 6. What you receive

- A zip named `<order_id>_<UTC time>.zip`, containing one folder with `telemetry.csv.gz`, `order.json`,
  `manifest.json`, `manifest.sig`, `pubkey.pem`.
- **Once, separately** (a call or a different message, not with the zip): our key fingerprint, a 64-character
  hex string. Write it down: every delivery must match it.

## 7. Verify

Unzip, then:
```bash
python verify_episode.py <unzipped folder> --order my_order.json --fingerprint <our key fingerprint>
```
The last line must be `RESULT: episode VALID`. Every check is listed with OK or FAIL:

| If this fails | It means |
|---|---|
| valid signature / signer key matches the expected fingerprint | not signed by our key, or the manifest was altered |
| hash of ... | a file was altered after signing |
| order executed is byte-identical to your order file | the recording is not of your order (or your copy changed) |
| recorded after the order was issued | the recording predates your order |
| point N ... reached | the robot did not reach your waypoint N |
| datasheet limits / allowed envelope / positions continuous | values that this robot cannot physically produce |
| run status | the run was interrupted |

No zip should ever be accepted with a FAIL line.

## 8. Using the data

`telemetry.csv.gz` (gzip CSV, one row per controller packet, ≈125 rows per second):

| Column | Meaning |
|---|---|
| `t_controller_ms` | controller clock, Unix ms — use for timing between samples (its absolute value is offset, see SECURITY.md §8) |
| `t_pc_ns` | recording PC clock at reception, Unix ns (NTP-synchronised) |
| `robot_mode` | 4 disabled, 5 enabled idle, 7 moving |
| `speed_scaling` | global speed % |
| `q1..q6` | joint positions, degrees |
| `qd1..qd6` | joint speeds, degrees/s |
| `i1..i6` | joint currents, controller units |
| `x y z rx ry rz` | tool pose, mm and degrees |

`manifest.json` adds robot metadata (model, firmware, hashed serial), statistics and the signer details.
How signing and keys work, and what is not proven yet: `SECURITY.md`.
