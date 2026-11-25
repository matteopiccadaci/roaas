# Security design: signatures, keys, TPM

**Status:** deliveries are currently signed with a software key (interim). The TPM-backed signer is
implemented and will be activated on a PC dedicated to the robot (see section 4).

This document describes exactly what the kit protects, how, and what it does **not** protect yet.

## 1. What is signed

Each episode has one `manifest.json`. It is serialised canonically (UTF-8, keys sorted, no whitespace) and
those exact bytes are signed. The manifest contains:

- the SHA-256 and size of every delivered file: `telemetry.csv.gz`, `order.json`, `pubkey.pem`;
- the SHA-256 and size of `raw.bin` (full controller stream), which the lab keeps;
- the order (id, nonce, waypoints, speed, `order_sha256` = SHA-256 of the order file as received);
- robot metadata, recording statistics, clock information, and the signer description.

Changing a single byte of any delivered file, or of the manifest, makes verification fail.

## 2. Algorithm and format

| Item | Value |
|---|---|
| Signature | ECDSA on curve NIST P-256 (secp256r1) with SHA-256 |
| Encoding | DER (ASN.1 `SEQUENCE { r, s }`), base64 in `manifest.sig` |
| Public key | `pubkey.pem`, PEM SubjectPublicKeyInfo |
| Key fingerprint | SHA-256 of the DER SubjectPublicKeyInfo, hex (also in `manifest.json` → `signer.pubkey_sha256`) |

Verification without this kit, using OpenSSL:
```bash
base64 -d manifest.sig > manifest.sig.der
openssl dgst -sha256 -verify pubkey.pem -signature manifest.sig.der manifest.json
openssl pkey -pubin -in pubkey.pem -outform DER | sha256sum      # = fingerprint
```

## 3. Key identity (pinning)

A signature only matters if you know whose key made it. The lab sends the key fingerprint **once, through
a separate channel**. The buyer passes it to `verify_episode.py --fingerprint ...`; every later delivery
must carry the same key. If the lab ever replaces the key, it announces the new fingerprint the same way.

## 4. Where the private key lives

### Windows TPM 2.0 (production setting)

The recording PC runs Windows 11 with a TPM 2.0 (Intel firmware TPM, "PTT"). `tpm_setup.ps1` uses the
Windows CNG API to create a key named `DobotEpisodeRecorder` in the **Microsoft Platform Crypto Provider**,
which is the TPM:

- algorithm ECDSA P-256, usage: signing only;
- generated inside the TPM with export policy **none** (non-exportable): the private key cannot be copied
  off the chip, not even by an administrator;
- each signature is computed by the TPM (`tpm_sign.ps1`); the kit only sees the result;
- only the public key leaves the TPM; it is delivered as `pubkey.pem` and the manifest records
  `signer.type = "windows-tpm"`.

Consequence: a valid signature means the manifest was signed **on that physical PC**. The key is bound
to the machine; a copy of the kit on another computer cannot produce it.

What it does not mean (see section 8): it does not prove *which program* asked the TPM to sign, and
the buyer cannot yet check cryptographically that the key really is inside a TPM.

### Software key (current interim setting)

Until a PC dedicated to the robot is in place, the lab signs with `--signer software`: the private key is
a PEM file (`keys/recorder_key.pem`) kept by the lab. The cryptography and the verification are identical,
and the manifest says `signer.type = "software"`, but the key is not hardware-protected: anyone who
obtained a copy of the file could sign. The key fingerprint is pinned in the same way (section 3).

When the dedicated PC is installed, the lab will create the TPM key there and send the buyer the new
fingerprint through a separate channel; from then on deliveries carry `signer.type = "windows-tpm"`.

## 5. Freshness: "this recording is new"

- The **buyer** chooses the waypoints and a random 128-bit nonce for every order.
- The lab stores the order file byte for byte (`order.json`) and its SHA-256 in the signed manifest.
- The verifier checks that the order executed is byte-identical to the buyer's own copy.
- It checks that the trajectory reaches every waypoint, in order: an old recording cannot match new,
  unpredictable waypoints.
- If the order has `issued_at_utc`, the verifier checks that the recording time comes after it.

## 6. Robot identity: the serial number

The robot's serial number is not published. The manifest carries
`serial_hmac_sha256 = HMAC-SHA256(lab secret, serial)`, where the lab secret is a random 256-bit key.

- It is a stable pseudonym: every episode from the same robot carries the same value.
- A plain SHA-256 of a serial would be reversible by trying all plausible serials; with a secret key
  it is not.
- The lab can reveal the mapping to a party of its choice by disclosing the serial and the key.

## 7. Physical plausibility checks (verifier)

On top of the cryptography, `verify_episode.py` checks that the data behaves like this robot:

- every joint position and speed within the CR3A datasheet limits;
- every position inside the lab's allowed envelope (recorded in the manifest);
- **continuity**: no joint moves farther between two samples than its maximum speed allows;
- controller timestamps strictly increasing; sample count as declared;
- the run completed (aborted runs are signed with `status: "aborted"` and fail verification).

## 8. Clocks

- `t_controller_ms` comes from the robot controller's own clock. On the current robot it is offset from
  real time (≈7 h); use it for sample timing only. The manifest records the measured PC-minus-controller
  offset (`stats.pc_minus_controller_clock_ms`).
- `t_pc_ns` and `recorded_at_utc` come from the recording PC, synchronised with NTP before recording.

## 9. Known limits and next steps

These are honest gaps, listed so we can design them together.

| Gap | Today | Planned |
|---|---|---|
| Proof that the key is in a TPM | stated by the lab, `signer.key_attestation = null` | TPM **key attestation**: an attestation key certifies the signing key; its chain leads to the TPM's endorsement key certificate, issued by the TPM manufacturer |
| State of the recording PC | not attested | **TPM quote** of the boot measurements (PCRs), signed by an attestation key, included with each delivery |
| Which program signed | any process in the Windows user session can ask the TPM to sign | TPM key protected by a policy tied to the quote above |
| Trusted recording time | PC clock (NTP) | RFC 3161 timestamp of the manifest hash from a public time-stamping authority |
| Synthetic data | the signature proves origin and integrity, not physics | cross-signal checks (currents vs accelerations, tool pose vs joint angles), raw stream audits on request |

## 10. Data kept by the lab

`raw.bin` (every 1440-byte packet from port 30004), the full episode folder and the order files are kept by
the lab. Their hashes are in each signed manifest, so any of them can be produced later for an audit and
checked against the manifest the buyer already holds.
