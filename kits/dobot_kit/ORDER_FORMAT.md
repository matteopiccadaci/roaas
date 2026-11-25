# Order format

An **order** is a small JSON file that tells the lab which motion to record. You choose the waypoints and a
random code (the *nonce*); nobody can know them in advance, so the recording that comes back is provably new.

## Workflow

```
 BUYER                                          LAB
 1. make_order.py / write the JSON by hand
 2. check_order.py  (same checks as the lab)
 3. email the order file ──────────────────▶   4. run_order.py: validate, run on the robot,
                                                   record, sign, verify, package
 6. verify_episode.py --order --fingerprint ◀── 5. delivery zip
```

Orders go by email, as an attachment, to **mapiccadaci@unime.it** (subject `Robot order <order_id>`).

## Fields

| Field | Required | Type | Rules |
|---|---|---|---|
| `order_id` | yes | string | 1–64 characters among `A–Z a–z 0–9 _ . -`. Your reference; it names the episode folder. |
| `nonce` | yes | string | Hexadecimal, 12–128 characters. **Use 32 hex characters (128 random bits) for purchases**, generated fresh for every order. |
| `speed_factor` | yes | integer | Global robot speed in percent, from 1 to the lab's `max_speed_factor`. |
| `waypoints` | yes | list | 1 to 10 points. Each point is a list of 6 numbers: joint angles **J1…J6 in degrees** (J1 = base, J6 = flange). Every value must lie inside the lab's `allowed_envelope_deg`. |
| `issued_at_utc` | no | string | When you issued the order, ISO 8601 UTC, e.g. `2026-11-18T10:00:00Z`. The verifier then checks that the recording was made after it. |
| `notes` | no | string | Free text, up to 500 characters. |

Unknown fields are ignored with a warning.

## What the robot does

1. Moves to the lab's home pose (`home_deg`, currently all joints at 0°, arm upright).
2. Moves to each waypoint in order with `MovJ` (joint-space interpolation, stopping at each point).
3. Returns to home.

The whole run is recorded at 125 Hz from the controller's real-time port (30004). Recording starts about
0.5 s before the first motion command and ends about 0.5 s after the robot is back home and disabled.

## Lab limits

The lab publishes its current limits in `lab_limits_cr3a.json`:

```json
{
  "home_deg": [0, 0, 0, 0, 0, 0],
  "max_speed_factor": 100,
  "suggested_speed_factor": 30,
  "allowed_envelope_deg": [[-20, 20], [-30, 30], [-45, 45], [-90, 90], [-90, 90], [-180, 180]]
}
```

Inside the envelope any combination of joint values is allowed. Orders outside it are rejected before the
robot moves. Speed can be anything from 1 to 100%; 30% is the suggested cautious default and can be changed in each order.

## Example

`example_order.json`:

```json
{
  "order_id": "buyer-demo-0001",
  "nonce": "3f9a6c1e8b2d47f0a5c9e1b7d3f20864",
  "issued_at_utc": "2026-11-18T10:00:00Z",
  "speed_factor": 30,
  "waypoints": [
    [12.5, -15.0, 25.0, 30.0, -40.0, 45.0],
    [-10.0, 18.0, -20.0, -50.0, 35.0, -90.0],
    [5.0, -22.0, 38.0, 70.0, 20.0, 120.0]
  ],
  "notes": "Example order: three waypoints, all joints moving."
}
```

## Making an order

Random order inside the limits (fresh nonce and timestamp included):
```bash
python make_order.py --limits lab_limits_cr3a.json --points 3 --speed 30 --out my_order.json
```

By hand: copy `example_order.json`, change `order_id`, put your own waypoints, and generate a nonce with
```bash
python -c "import secrets; print(secrets.token_hex(16))"
```

Check it before sending:
```bash
python check_order.py my_order.json --limits lab_limits_cr3a.json
```

**Keep your copy of the order file unchanged**: the verifier compares its exact bytes with the order the lab executed.

## What you receive

A zip `<order_id>_<UTC time>.zip` containing one folder with:

| File | Content |
|---|---|
| `telemetry.csv.gz` | One row per controller packet (≈125 per second): `t_controller_ms`, `t_pc_ns`, `robot_mode`, `speed_scaling`, joint positions `q1..q6` (deg), joint speeds `qd1..qd6` (deg/s), joint currents `i1..i6`, tool pose `x y z` (mm) `rx ry rz` (deg) |
| `order.json` | The order exactly as the lab received it |
| `manifest.json` | Metadata, statistics, SHA-256 of every file, signer details |
| `manifest.sig` | Signature of `manifest.json` (see SECURITY.md) |
| `pubkey.pem` | Public key of the recorder |

The full raw recording (`raw.bin`, every 1440-byte controller packet) stays with the lab; its SHA-256 is in the
manifest so it can be audited later.

## Verifying

```bash
python verify_episode.py <unzipped folder> --order my_order.json --fingerprint <key sha256 from the lab>
```

The fingerprint is sent to you by the lab through a separate channel (e.g. a call or a different email),
once. It must match on every delivery.
