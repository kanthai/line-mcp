#!/usr/bin/env python3
"""
LINE E2EE media decryption — offline, no Frida needed.

Input:
  blob_path : path to raw CDN blob (C[file_size] || HMAC-SHA256[32])
  km_hex    : 32-byte key material (hex) — plaintext KM from e2ee.db

Output: decrypted JPEG/audio/file written to out_path
"""
from __future__ import annotations
import argparse, hashlib, hmac, sys
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def derive_keys(km: bytes):
    """HKDF-SHA256(KM, info="FileEncryption", L=76) → Kenc[32], Kmac[32], IV[12]"""
    derived = HKDF(
        algorithm=hashes.SHA256(), length=76,
        salt=None, info=b"FileEncryption",
        backend=default_backend()
    ).derive(km)
    return derived[:32], derived[32:64], derived[64:76]


def decrypt(blob: bytes, km: bytes) -> bytes:
    if len(km) != 32:
        raise ValueError(f"KM must be 32 bytes, got {len(km)}")
    if len(blob) < 33:
        raise ValueError("Blob too short")

    C   = blob[:-32]
    MAC = blob[-32:]

    Kenc, Kmac, IV = derive_keys(km)

    mac_computed = hmac.new(Kmac, C, hashlib.sha256).digest()
    if mac_computed != MAC:
        raise ValueError(
            f"HMAC-SHA256 mismatch\n"
            f"  expected: {MAC.hex()}\n"
            f"  computed: {mac_computed.hex()}"
        )

    cipher = Cipher(algorithms.AES(Kenc), modes.CTR(IV + b'\x00\x00\x00\x00'),
                    backend=default_backend())
    return cipher.decryptor().update(C)


def main():
    ap = argparse.ArgumentParser(description="Decrypt a LINE E2EE media blob")
    ap.add_argument("blob",   help="Path to raw CDN blob")
    ap.add_argument("km_hex", help="32-byte KM as hex (from e2ee.db)")
    ap.add_argument("out",    help="Output file path")
    args = ap.parse_args()

    blob = open(args.blob, "rb").read()
    km   = bytes.fromhex(args.km_hex)

    print(f"[*] blob={len(blob)}B  km={km.hex()[:16]}...", file=sys.stderr)
    plaintext = decrypt(blob, km)
    print(f"[*] decrypted={len(plaintext)}B  magic={plaintext[:4].hex()}", file=sys.stderr)

    open(args.out, "wb").write(plaintext)
    print(f"[+] saved → {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
