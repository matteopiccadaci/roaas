# Dobot CR3A episode kit — v0.4.4

Run a short scripted motion on a DOBOT CR3A, record the controller's real-time feedback at 125 Hz,
sign the result, and let the buyer verify it independently.

- **Buyer**: start with [BUYER_GUIDE.md](BUYER_GUIDE.md); order details in [ORDER_FORMAT.md](ORDER_FORMAT.md)
- **Lab**: run the order on the robot and deliver a signed zip → this README
- **Cryptography, keys, TPM, known limits** → [SECURITY.md](SECURITY.md)

Tested on a real CR3A (controller firmware 4.5.1.1) from Windows 11 and macOS, and on the included fake controller.

## Setup (both sides)

```bash
python -m venv .venv
# Windows PowerShell:  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass; .venv\Scripts\Activate.ps1
# macOS / Linux:       source .venv/bin/activate
pip install cryptography
```
On Windows use `python` (not `py`) once the virtual environment is active.

## Buyer: try the whole flow without a robot

```bash
python check_order.py example_order.json --limits lab_limits_cr3a.json
python fake_dobot.py                                    # in a second terminal
python run_order.py --order example_order.json --robot-info lab_limits_cr3a.json
python verify_episode.py episodes/<new folder> --order example_order.json
```
`run_order.py` also writes the zip the lab would send you, in `deliveries/`.

## Lab: running a buyer's order

1. Robot powered, no alarm, **TCP mode** selected in DobotStudio Pro, DobotStudio Pro disconnected.
2. Read-only check: `python probe.py --host <IP>`
3. Run, verify and package:
   ```bash
   python run_order.py --order buyer_order.json --robot-info robot_info_cr3a.json --host <IP> --live
   ```
   This signs with the software key (current interim setting). On the dedicated, TPM-equipped PC add
   `--signer tpm`:
   ```bash
   python run_order.py --order buyer_order.json --robot-info robot_info_cr3a.json --host <IP> --live --signer tpm
   ```
   The program is printed; the operator types `ENABLE` with the emergency stop within reach.
   At the end the robot is stopped and disabled, the episode is verified and the delivery zip is written
   to `deliveries/`. `raw.bin` stays in `episodes/`.

### One-time TPM key setup (dedicated Windows PC, when available)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tpm_setup.ps1
```
It creates (or reuses) the TPM key `DobotEpisodeRecorder` and prints the key storage provider,
which must be `Microsoft Platform Crypto Provider`. Then send the buyer the key fingerprint, which `run_order.py`
prints after every run (`signer key sha256`).

## Files

| File | Side | Purpose |
|---|---|---|
| `BUYER_GUIDE.md` | buyer | Step-by-step guide for the buyer |
| `ORDER_FORMAT.md`, `example_order.json`, `lab_limits_cr3a.json` | buyer | Order specification, example, the lab's current limits |
| `make_order.py` | buyer | Random order inside the limits (fresh nonce and timestamp) |
| `check_order.py` | buyer | Same validation the lab applies, before sending |
| `verify_episode.py` | buyer | Signature, key fingerprint, hashes, order match, waypoints, limits, envelope, continuity |
| `run_order.py` | lab | Validate → run → verify → delivery zip |
| `episode.py` | lab | Runs the motion and records/signs the episode (used by `run_order.py`) |
| `probe.py` | lab | Read-only check of the robot (mode, alarms, angles, stream rate, clock offset) |
| `signers.py`, `tpm_setup.ps1`, `tpm_sign.ps1` | lab | Software or Windows-TPM signing |
| `orders.py`, `dobot_proto.py` | both | Order validation; layout of the 1440-byte controller packet |
| `fake_dobot.py` | both | Fake controller (ports 29999 + 30004) for tests without a robot |
| `summary.py`, `inspect_raw.py` | lab | Episode summary; state-by-state diagnosis of `raw.bin` |
| `order_microtest.json`, `order_visible_j6.json`, `order_all_joints_*.json` | lab | Commissioning orders |

## Controller notes (CR3A, firmware 4.5.1.1)

- `RequestControl()` does not exist (returns `-10000`): select TCP mode in DobotStudio Pro instead.
  In online mode the dashboard (29999) does not answer; port 30004 streams in both modes.
- `MovJ(joint={j1,...,j6})` on port 29999 works. There is no port 30003 on V4 controllers.
- Over Wi-Fi the controller occasionally stalls (~150 ms) and sends a truncated packet. The recorder frames
  packets on the fixed TestValue at byte 48 and accepts a frame only if the next one is aligned too, so
  damaged frames are dropped and counted in the manifest. **Use a cable for production recordings.**
- Motion completion is read from the dashboard (`RobotMode()`: 5 idle, 7 running).
- The controller clock may be far from real time (≈7 h on the tested unit): see SECURITY.md, section 8.
