"""Signing backends for episode manifests. Both produce ECDSA P-256 / SHA-256 signatures,
DER-encoded, so verification is identical whichever backend signed.

- SoftwareSigner: private key in a PEM file (keys/recorder_key.pem).
- WindowsTpmSigner: private key inside the Windows TPM 2.0 (Microsoft Platform Crypto Provider),
  non-exportable; signing goes through tpm_sign.ps1. Create the key once with tpm_setup.ps1.
"""
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

KIT_DIR = Path(__file__).resolve().parent


def spki_der(public_key):
    return public_key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def fingerprint(public_key):
    """SHA-256 of the DER SubjectPublicKeyInfo, hex. Exchange it out of band to pin the key."""
    return hashlib.sha256(spki_der(public_key)).hexdigest()


class SoftwareSigner:
    kind = "software"
    description = "ECDSA P-256 key in a PEM file kept by the lab (interim, not hardware-backed)"

    def __init__(self, keys_dir):
        path = Path(keys_dir) / "recorder_key.pem"
        if path.exists():
            self.key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.key = ec.generate_private_key(ec.SECP256R1())
            path.write_bytes(self.key.private_bytes(serialization.Encoding.PEM,
                                                    serialization.PrivateFormat.PKCS8,
                                                    serialization.NoEncryption()))
            os.chmod(path, 0o600)
            print(f"[key] created a new software key in {path} (NOT protected by a TPM).")
            print("      Back it up: if it is lost, deliveries will carry a different key fingerprint.")
        self.cert_der = None

    def public_key(self):
        return self.key.public_key()

    def sign(self, data):
        return self.key.sign(data, ec.ECDSA(hashes.SHA256()))


def ecc_public_blob_to_key(blob):
    """Windows BCRYPT_ECCPUBLIC_BLOB (magic, key length, X, Y) -> cryptography public key (P-256)."""
    magic, n = int.from_bytes(blob[0:4], "little"), int.from_bytes(blob[4:8], "little")
    if magic != 0x31534345 or n != 32:  # 'ECS1' = ECDSA P-256 public
        raise ValueError(f"not an ECDSA P-256 public blob (magic {magic:#x}, length {n})")
    x, y = int.from_bytes(blob[8:40], "big"), int.from_bytes(blob[40:72], "big")
    return ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()


class WindowsTpmSigner:
    kind = "windows-tpm"
    description = ("ECDSA P-256 key generated inside the Windows TPM 2.0 "
                   "(Microsoft Platform Crypto Provider), non-exportable")

    def __init__(self, keys_dir):
        keys_dir = Path(keys_dir)
        name_file, blob_file = keys_dir / "tpm_keyname.txt", keys_dir / "recorder_tpm_pub.blob"
        if not name_file.exists() or not blob_file.exists():
            raise SystemExit("TPM key not set up: run  powershell -NoProfile -ExecutionPolicy Bypass -File tpm_setup.ps1")
        self.key_name = name_file.read_text().strip()
        self._pub = ecc_public_blob_to_key(blob_file.read_bytes())
        self.cert_der = None

    def public_key(self):
        return self._pub

    def sign(self, data):
        with tempfile.TemporaryDirectory() as d:
            inp, out = Path(d) / "in.bin", Path(d) / "sig.bin"
            inp.write_bytes(data)
            r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                "-File", str(KIT_DIR / "tpm_sign.ps1"),
                                "-KeyName", self.key_name, "-InFile", str(inp), "-OutFile", str(out)],
                               capture_output=True, text=True)
            if r.returncode != 0 or not out.exists():
                raise RuntimeError(f"TPM signing failed: {r.stderr.strip() or r.stdout.strip()}")
            raw = out.read_bytes()
        if len(raw) != 64:
            raise RuntimeError(f"unexpected TPM signature length {len(raw)} (expected 64)")
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        self.public_key().verify(der, data, ec.ECDSA(hashes.SHA256()))  # sanity check before use
        return der


def make_signer(kind, keys_dir):
    if kind == "software":
        return SoftwareSigner(keys_dir)
    if kind == "tpm":
        return WindowsTpmSigner(keys_dir)
    raise SystemExit(f"unknown signer {kind}")
